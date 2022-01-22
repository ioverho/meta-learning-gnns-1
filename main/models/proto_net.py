import pytorch_lightning as pl
import torch
import torch.nn.functional as func
from torch import optim

from models.gat_base import get_subgraph_batch, get_classify_mask
from models.gat_encoder_sparse_pushkar import SparseGATLayer
from models.train_utils import *


class ProtoNet(pl.LightningModule):

    # noinspection PyUnusedLocal
    def __init__(self, input_dim, hidden_dim, feat_reduce_dim, lr, batch_size, f1_target_label):
        """
        Inputs
            proto_dim - Dimensionality of prototype feature space
            lr - Learning rate of Adam optimizer
        """
        super().__init__()
        self.save_hyperparameters()
        self.model = SparseGATLayer(in_features=input_dim, out_features=hidden_dim,
                                    feat_reduce_dim=feat_reduce_dim)

    def configure_optimizers(self):
        optimizer = optim.AdamW(self.parameters(), lr=self.hparams.lr)
        scheduler = optim.lr_scheduler.MultiStepLR(optimizer, milestones=[140, 180], gamma=0.1)
        return [optimizer], [scheduler]

    @staticmethod
    def calculate_prototypes(features, targets):
        """
        Given a stack of features vectors and labels, return class prototypes.

        :param features:
        :param targets:
        :return:
        """

        # features shape: [N, proto_dim], targets shape: [N]

        # Determine which classes we have
        classes, _ = torch.unique(targets).sort()
        prototypes = []
        for c in classes:
            # get all node features for this class and average them
            # noinspection PyTypeChecker
            p = features[torch.where(targets == c)[0]].mean(dim=0)
            prototypes.append(p)
        prototypes = torch.stack(prototypes, dim=0)
        # Return the 'classes' tensor to know which prototype belongs to which class
        return prototypes, classes

    @staticmethod
    def classify_features(prototypes, classes, feats, targets):
        """
        Classify new examples with prototypes and return classification error.

        :param prototypes:
        :param classes:
        :param feats:
        :param targets:
        :return:
        """
        # Squared euclidean distance
        dist = torch.pow(prototypes[None, :] - feats[:, None], 2).sum(dim=2)
        predictions = func.log_softmax(-dist, dim=1)
        # noinspection PyUnresolvedReferences
        labels = (classes[None, :] == targets[:, None]).long().argmax(dim=-1)
        return predictions, labels, accuracy(predictions, labels)

    def calculate_loss(self, batch, mode):
        """
        Determine training loss for a given support and query set.

        :param batch:
        :param mode:
        :return:
        """

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        support_graphs, query_graphs, support_targets, query_targets = batch

        x, edge_index = get_subgraph_batch(support_graphs)
        support_feats = self.model(x, edge_index).squeeze()

        # select only the features for the nodes we actually want to classify and compute prototypes for these
        support_feats = support_feats[get_classify_mask(support_graphs)]

        assert support_feats.shape[0] == support_targets.shape[0], \
            "Nr of features returned does not equal nr. of classification nodes!"

        prototypes, classes = ProtoNet.calculate_prototypes(support_feats, support_targets)

        x, edge_index = get_subgraph_batch(query_graphs)
        query_feats = self.model(x, edge_index).squeeze()
        query_feats = query_feats[get_classify_mask(query_graphs)]

        assert query_feats.shape[0] == query_targets.shape[0], \
            "Nr of features returned does not equal nr. of classification nodes!"

        predictions, targets, acc = ProtoNet.classify_features(prototypes, classes, query_feats, query_targets)

        meta_loss = func.cross_entropy(predictions, targets)

        if mode == 'train':
            self.log(f"{mode}_loss", meta_loss)

        f1, f1_macro, f1_micro, acc = evaluation_metrics(predictions, targets, self.hparams['f1_target_label'])
        self.log_on_epoch(f"{mode}_accuracy", acc)
        self.log_on_epoch(f'{mode}_train_f1', f1)
        self.log_on_epoch(f"{mode}_f1_macro", f1_macro)
        self.log_on_epoch(f"{mode}_f1_micro", f1_micro)

        return meta_loss

    def log_on_epoch(self, metric, value):
        self.log(metric, value, on_step=False, on_epoch=True)

    def log(self, metric, value, on_step=True, on_epoch=False, **kwargs):
        super().log(metric, value, on_step=on_step, on_epoch=on_epoch, batch_size=self.hparams['batch_size'])

    def training_step(self, batch, batch_idx):
        return self.calculate_loss(batch, mode="train")

    def validation_step(self, batch, batch_idx):
        _ = self.calculate_loss(batch, mode="val")

    def test_step(self, batch, batch_idx1, batch_idx2):
        _ = self.calculate_loss(batch, mode="test")
