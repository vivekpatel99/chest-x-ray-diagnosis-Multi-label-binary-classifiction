
from tensorflow.keras.applications.densenet import DenseNet121, DenseNet201
from tensorflow.keras.layers import (
    Activation,
    BatchNormalization,
    Dense,
    Dropout,
    GlobalAveragePooling2D,
)
from tensorflow.keras.models import Model
from tensorflow.keras.regularizers import l2


def build_DenseNet121(input_shape:tuple, num_classes:int):
    base_model = DenseNet121(
            include_top=False,
            weights='imagenet', # input will be grayscale images
            input_shape=input_shape  
        )
    base_model.trainable = True

    # Then freeze all layers except the last 20
    for layer in base_model.layers[:-40]:
        layer.trainable = False
        
    x = base_model.output
    x = GlobalAveragePooling2D()(x)
    x = Dropout(0.5)(x)
    x = Dense(512, activation='relu',kernel_regularizer=l2(0.001))(x)
    x = Dropout(0.5)(x)
    x = Dense(units=int(num_classes), name='final_dense',
              kernel_regularizer=l2(0.001))(x)
    
    # activation must be float32 for metrics such as f1 score and so on
    predictions = Activation('sigmoid', dtype='float32', name='predictions')(x)

    return  Model(inputs=base_model.input, outputs=predictions)
