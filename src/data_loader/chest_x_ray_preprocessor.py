import logging

import pandas as pd
import tensorflow as tf
from sklearn.model_selection import train_test_split

from src.utils.logs import get_logger


class ChestXRayPreprocessor:
    def __init__(self, config, labels:list|None=None) -> None:
        """
        Initializes the ChestXRayPreprocessor class with configuration settings.

        Args:
            config: Configuration object containing various settings for training and dataset directories.
            labels (list, optional): List of labels to use for classification. If not provided, defaults to 
                                    ['Atelectasis', 'Effusion', 'Infiltration', 'Mass', 'Nodule'].

        Attributes:
            batch_size (int): The batch size for training, derived from the config.
            dataset_len (int): The length of the dataset, initialized to 0.
            LABELS (list): List of labels for classification.
            TRAIN_CSV_LABELS (list): List of labels for the training CSV file.
            normalization_layer (tf.keras.layers.Normalization): Normalization layer for preprocessing images.
            data_augmentation (tf.keras.Sequential): Sequential model for data augmentation, including random 
                                                    rotation, translation, and zoom.
        """
        self.log = get_logger(__name__, log_level=logging.INFO)
        self.config = config
        self.batch_size:int = config.TRAIN.BATCH_SIZE
        self.dataset_len:int = 0 
        self.image_size:int = config.TRAIN.IMG_SIZE
        self.pos_weights = None
        self.neg_weights = None
        self.test_images = None
        self.test_labels = None
        self.labels = labels 

        self.TRAIN_CSV_LABELS = ['Image Index', 'Finding Labels']

        self.normalization_layer = tf.keras.layers.Normalization()
        self.data_augmentation = tf.keras.Sequential([
            tf.keras.layers.RandomRotation(0.10, fill_mode='constant', seed=49),
            tf.keras.layers.RandomTranslation(0.05, 0.05, fill_mode='constant', seed=49),
            tf.keras.layers.RandomZoom(0.1, 0.1, fill_mode='constant', seed=49),
            tf.keras.layers.GaussianNoise(0.02, seed=49)  # Simulates quantum noise
        ])

    @tf.function
    def load_image(self, image_name, label)-> tuple[tf.Tensor, tf.Tensor]:
        """Loads and preprocesses an image."""
        img_dir = self.config.DATASET_DIRS.TRAIN_IMAGES_DIR 
        full_path = tf.strings.join([img_dir, '/', image_name])
        image = tf.io.read_file(full_path)
        image = tf.io.decode_jpeg(image, channels=3)

        image = tf.keras.preprocessing.image.smart_resize(image, 
                                [self.image_size, self.image_size])

        image = tf.image.convert_image_dtype(image, dtype=tf.float32)
        label = tf.cast(label, tf.float32)
        return image, label
    
    @tf.function
    def augment_image(self, image, label)-> tuple[tf.Tensor, tf.Tensor]:
        """Applies data augmentation to an image."""
        self.log.info("Augmenting image")
        return self.data_augmentation(image), label
    
    @tf.function
    def normalize_image(self, image, label)-> tuple[tf.Tensor, tf.Tensor]:
        self.log.info("Normalizing image")
        # image = self.normalization_layer(image)
        image = tf.keras.applications.densenet.preprocess_input(image)
        return image, label


    def prepare_dataset(self, dataset, batch_size, is_training=False)-> tf.data.Dataset:
        """Prepares a dataset for training or evaluation."""
        dataset = dataset.map(self.normalize_image, num_parallel_calls=tf.data.AUTOTUNE)
        if is_training:
            self.log.info("Preparing training dataset with augmentation")
            # dataset = dataset.cache()
            dataset = dataset.shuffle(buffer_size=self.dataset_len) # shuffle before repeat
            
            # creates circle by joing start and end of dataset and roll it using batch size
            dataset = dataset.repeat() # repeat before batch
            dataset = dataset.batch(batch_size) # batch before cache
            dataset = dataset.map(self.augment_image, num_parallel_calls=tf.data.AUTOTUNE) # augmentation before batch
        else:
            # dataset = dataset.cache()
            dataset = dataset.batch(batch_size)

        return  dataset.prefetch(buffer_size=tf.data.AUTOTUNE)

    def get_class_weights(self, labels_df)-> tuple[tf.Tensor, tf.Tensor]:
        """Calculates class weights."""
        N = labels_df.shape[0]
        positive_frequencies = (labels_df == 1).sum() / N
        negative_frequencies = (labels_df == 0).sum() / N
        pos_weights = negative_frequencies.values
        neg_weights = positive_frequencies.values
        return pos_weights, neg_weights

    def train_df_clean_up(self, df)-> tuple[pd.DataFrame, pd.DataFrame]:
        """Cleans up the training dataframe."""
        self.log.info("Cleaning up training dataframe")
        filterd_labels_df = df[self.TRAIN_CSV_LABELS[1]].str.get_dummies(sep='|').astype('float32')
        # train_images_df = train_df['Image Index'] 
        filterd_labels_df = filterd_labels_df[self.labels]

        # Drop rows where ALL labels are zero:
        all_labels = filterd_labels_df.columns
        all_labels_zero = (filterd_labels_df[all_labels] == 0).all(axis=1)
        filterd_labels_df = filterd_labels_df[~all_labels_zero] # ~ is the negation operator

        images_df = df['Image Index'] 
        filtered_images_df = images_df[~all_labels_zero]
        
        self.pos_weights, self.neg_weights = self.get_class_weights(filterd_labels_df)

        return filtered_images_df, filterd_labels_df

    def _normalization_layer_adapt(self, train_ds:tf.data.Dataset) -> None:
        """Adapts the normalization layer to the training data."""
        normalizing_size = int(self.dataset_len*0.30)
        images_for_stats = train_ds.map(lambda x, _: x, 
                                    num_parallel_calls=tf.data.AUTOTUNE)\
                                .unbatch()\
                                .batch(normalizing_size)\
                                .take(1)  # Take just one batch
        self.normalization_layer.adapt(images_for_stats)
        self.log.info("Normalization layer adapted")

    def load_and_preprocess_dataframe(self, csv_path: str, is_training: bool, split_ratio: float = 0.2) -> tuple[tf.data.Dataset, tf.data.Dataset] | tf.data.Dataset:
        """Loads a dataframe from CSV, preprocesses it, and returns a tf.data.Dataset.
        Args:
            csv_path (str): Path to the CSV file.
            is_training (bool): Whether the dataset is for training.
            split_ratio (float): The ratio of the dataset to be used for validation.
        Returns:
            tuple[tf.data.Dataset, tf.data.Dataset] | tf.data.Dataset: If is_training is True, returns a tuple of (train_dataset, validation_dataset).
                                                                        Otherwise, returns the test_dataset.
        """
        self.log.info("Loading and preprocessing dataframe")

        df = pd.read_csv(csv_path, usecols=self.TRAIN_CSV_LABELS)

        images_df, labels_df = self.train_df_clean_up(df)

        self.log.info(f"Loaded dataframe with shape: {df.shape} and {len(df)} rows")
        train_images, val_images, train_labels, val_labels  = train_test_split(images_df.values, 
                                                                                labels_df.values, 
                                                                                 test_size=split_ratio,
                                                                                random_state=42, 
                                                                                stratify=labels_df.values)
    
        # Split the data into training and validation sets
        # rest_images, self.test_images, rest_labels, self.test_labels = train_test_split(images_df.values, 
        #                                                                         labels_df.values, 
        #                                                                         test_size=0.1, 
        #                                                                         random_state=42, 
        #                                                                         stratify=labels_df.values)
    
        # train_images, val_images, train_labels, val_labels = train_test_split(rest_images, 
        #                                                                         rest_labels, 
        #                                                                         test_size=split_ratio, 
        #                                                                         random_state=42, 
        #                                                                         stratify=rest_labels)
        self.dataset_len = len(train_images)
        self.log.info(f"training split: {len(train_images)} and validation split: {len(val_images)}")

        train_dataset = tf.data.Dataset.from_tensor_slices((train_images, train_labels))
        val_dataset = tf.data.Dataset.from_tensor_slices((val_images, val_labels))

        train_dataset = train_dataset.map(lambda x, y: self.load_image(x, y), num_parallel_calls=tf.data.AUTOTUNE)
        val_dataset = val_dataset.map(lambda x, y: self.load_image(x, y), num_parallel_calls=tf.data.AUTOTUNE)

        return train_dataset, val_dataset


    def get_training_and_validation_datasets(self, batch_size: int | None = None) -> tuple[
        tf.data.Dataset, tf.data.Dataset, tf.Tensor| None, tf.Tensor | None, int]:
        """Loads, preprocesses, and prepares training and validation datasets.

        Returns:
            A tuple containing the training dataset, the validation dataset, positive weights, and negative weights.
        """
        self.log.info(f"Getting training and validation datasets with batch size:{batch_size}")
        train_ds, valid_ds = self.load_and_preprocess_dataframe(self.config.DATASET_DIRS.TRAIN_CSV, is_training=True)

        self._normalization_layer_adapt(train_ds=train_ds)

        if not batch_size:
            batch_size = self.config.TRAIN.BATCH_SIZE

        train_ds = self.prepare_dataset(train_ds, batch_size, is_training=True)
        valid_ds = self.prepare_dataset(valid_ds, batch_size, is_training=False)
        steps_per_epoch = self.dataset_len // batch_size
        return train_ds, valid_ds, self.pos_weights, self.neg_weights, steps_per_epoch


    def get_test_dataset(self, batch_size: int | None = None) -> tf.data.Dataset:
        """Loads, preprocesses, and prepares the test dataset."""
        self.log.info("Getting test dataset")
        # df = pd.read_csv(self.config.DATASET_DIRS.TEST_CSV)

        # image = df.Image
        # labels_df = df[self.LABELS]

        dataset = tf.data.Dataset.from_tensor_slices((self.test_images, self.test_labels))
        test_ds = dataset.map(lambda x, y: self.load_image(x, y), num_parallel_calls=tf.data.AUTOTUNE)
        if not batch_size:
            batch_size = self.config.TRAIN.BATCH_SIZE

        test_ds = self.prepare_dataset(test_ds, batch_size, is_training=False)
        return test_ds
    



    # def prepare_dataset(self, dataset, batch_size, is_training=False)-> tf.data.Dataset:
    #     """Create dataset (from files, etc.)
    #         Shuffle (for training)
    #         Pre-processing (normalization, etc.)
    #         Cache (if data fits in memory)
    #         Repeat (for training)
    #         Augmentation (for training)
    #         Batch
    #         Prefetch
    #     """
    #     if is_training:
    #         self.log.info("Preparing training dataset with augmentation")
    #         # Shuffling before repeating ensures that each epoch sees a different order of the data. 
    #         # If you repeat first, you'll get the same order multiple times before it shuffles.
    #         dataset = dataset.shuffle(buffer_size=self.dataset_len) # shuffle before repeat
            
    #         # makes the dataset loop indefinitely. If you batch before repeating, 
    #         # you'll only repeat the batches, not the individual samples.
    #         dataset = dataset.repeat() # repeat before batch
    #         # Data augmentation should be applied to individual images, not to batches. 
    #         # Applying augmentation after batching would mean that 
    #         # the same augmentation is applied to all images in a batch, which is not the desired behavior.
    #         dataset = dataset.map(self.augment_image, num_parallel_calls=tf.data.AUTOTUNE) # augmentation before batch
    #         dataset = dataset.map(self.normalize_image, num_parallel_calls=tf.data.AUTOTUNE)
    #         dataset = dataset.batch(batch_size) # batch before cache
    #         # Caching after batching is more efficient. You're caching the batches, which are the units of data 
    #         # that will be used during training. Caching individual images and then batching would be less efficient.
    #         # dataset = dataset.cache() # cache after batch
    #         # prefetch should always be the last operation in your pipeline. It overlaps data preprocessing with model execution, 
    #         # so it needs to happen after all other transformations.
    #     else:
    #         dataset = dataset.map(self.normalize_image, num_parallel_calls=tf.data.AUTOTUNE)
    #         dataset = dataset.batch(batch_size)
    #         # dataset = dataset.cache()

    #     return  dataset.prefetch(buffer_size=tf.data.AUTOTUNE)