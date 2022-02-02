import torch.cuda

from data_prep.graph_dataset import TorchGeomGraphDataset
from samplers.batch_sampler import FewShotSampler
from samplers.graph_sampler import KHopSampler
from samplers.maml_batch_sampler import FewShotMamlSampler

SUPPORTED_DATASETS = ['gossipcop', 'twitterHateSpeech']


def get_data(data_train, data_eval, model, hop_size, top_k, top_users_excluded,
             k_shot, train_split_size, eval_split_size, feature_type, vocab_size, dirs, num_workers=None):
    """
    Creates and returns the correct data object depending on data_name.
    Args:
        data_train (str): Name of the data corpus which should be used for training.
        data_eval (str): Name of the data corpus which should be used for testing/evaluation.
        model (str): Name of the model should be used.
        hop_size (int): Number of hops used to create sub graphs.
        top_k (int): Number of top users to be used in graph.
        k_shot (int): Number of examples used per task/batch.
        train_split_size (tuple): Floats defining the size of test/train/val for the training dataset.
        eval_split_size (tuple): Floats defining the size of test/train/val for the evaluation dataset.
        feature_type (int): Type of features that should be used.
        vocab_size (int): Size of the vocabulary.
        dirs (str): Path to the data (full & complete) to be used to create the graph (feature file, edge file etc.)
        num_workers (int): Amount of workers for parallel processing.
    Raises:
        Exception: if the data_name is not in SUPPORTED_DATASETS.
    """

    if data_train not in SUPPORTED_DATASETS:
        raise ValueError(f"Train data with name '{data_train}' is not supported.")

    if data_eval not in SUPPORTED_DATASETS:
        raise ValueError(f"Eval data with name '{data_eval}' is not supported.")

    if data_train == data_eval:
        assert train_split_size[0] > 0.0 and train_split_size[1] and train_split_size[2] > 0.0, \
            "Data for training and evaluation is equal and one of the split sizes is 0!"

    num_workers = num_workers if num_workers is not None else 4 if torch.cuda.is_available() else 0  # mac has 8 CPUs

    data_config = {'top_users': top_k, 'top_users_excluded': top_users_excluded, 'feature_type': feature_type,
                   'vocab_size': vocab_size}

    # creating a train and val loader from the train dataset
    train_config = {**data_config, **{'data_set': data_train, 'train_size': train_split_size[0],
                                      'val_size': train_split_size[1], 'test_size': train_split_size[2]}}
    graph_data_train = TorchGeomGraphDataset(train_config, train_split_size, *dirs)

    train_loader = get_loader(graph_data_train, model, hop_size, k_shot, num_workers, 'train')
    train_val_loader = get_loader(graph_data_train, model, hop_size, k_shot, num_workers, 'val')

    train_labels = graph_data_train.labels
    train_graph_size = graph_data_train.size
    train_class_ratio = graph_data_train.class_ratio
    train_b_size = train_loader.b_size
    eval_graph_size = None

    print(f"\nTrain graph size: \n num_features: {train_graph_size[1]}\n total_nodes: {train_graph_size[0]}")

    if data_train == data_eval:
        print(f'\nData eval and data train are equal, loading graph data only once.')
        graph_data_eval = graph_data_train
    else:
        # creating a val and test loader from the eval dataset
        data_config['top_users_excluded'] = 0

        eval_config = {**data_config, **{'data_set': data_eval, 'train_size': eval_split_size[0],
                                         'val_size': eval_split_size[1], 'test_size': eval_split_size[2]}}

        graph_data_eval = TorchGeomGraphDataset(eval_config, eval_split_size, *dirs)

        eval_graph_size = graph_data_eval.size
        print(f"\nTest graph size: \n num_features: {eval_graph_size[1]}\n total_nodes: {eval_graph_size[0]}")

    test_loader = get_loader(graph_data_eval, model, hop_size, k_shot, num_workers, 'test')
    test_val_loader = get_loader(graph_data_eval, model, hop_size, k_shot, num_workers, 'val')
    eval_labels = graph_data_eval.labels

    loaders = (train_loader, train_val_loader, test_loader, test_val_loader)
    labels = (train_labels, eval_labels)

    f1_train_label, f1_eval_label = graph_data_train.f1_target_label, graph_data_eval.f1_target_label

    return loaders, train_graph_size, eval_graph_size, labels, train_b_size, train_class_ratio, \
           (f1_train_label, f1_eval_label)


def get_loader(graph_data, model, hop_size, k_shot, num_workers, mode):
    n_classes = len(graph_data.labels)

    mask = graph_data.mask(f"{mode}_mask")
    shuffle = mode == 'train'
    shuffle_once = mode == 'val'

    if model in ['gat', 'prototypical']:
        batch_sampler = FewShotSampler(graph_data.data.y, mask, n_way=n_classes, k_shot=k_shot, include_query=True,
                                       shuffle=shuffle, shuffle_once=shuffle_once)
    elif model == 'gmeta':
        batch_sampler = FewShotMamlSampler(graph_data.data.y, mask, n_way=n_classes,
                                           k_shot=k_shot, include_query=True)

    else:
        raise ValueError(f"Model with name '{model}' is not supported.")

    sampler = KHopSampler(graph_data, model, batch_sampler, n_classes, k_shot, hop_size, num_workers=num_workers)

    print(f"\n{mode} sampler amount of episodes / batches: {len(sampler)}")

    # no need to wrap it again in a dataloader
    return sampler
