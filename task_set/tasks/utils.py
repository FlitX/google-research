# coding=utf-8
# Copyright 2020 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# python3
"""Utilities for sampling and creating different types of Tasks."""

import copy
import functools
import json
from typing import Callable, Dict, Tuple, Text, Any, Optional
import numpy as np
import sonnet as snt

from task_set import datasets
import tensorflow.compat.v1 as tf


def sample_log_int(rng, low, high):
  """Sample an integer logrithmically between `low` and `high`."""
  sample = rng.uniform(np.log(float(low)), np.log(float(high)))
  return int(np.round(np.exp(sample)))


def sample_linear_int(rng, low, high):
  """Sample an integer linearly between `low` and `high`."""
  sample = rng.uniform(float(low), float(high))
  return int(np.round(sample))


def sample_log_float(rng, low,
                     high):
  """Sample a float value logrithmically between `low` and `high`."""
  return float(np.exp(rng.uniform(np.log(float(low)), np.log(float(high)))))


def sample_bool(rng, p):
  """Sample a boolean that is True `p` percent of time."""
  if not 0.0 <= p <= 1.0:
    raise ValueError("p must be between 0 and 1.")
  return rng.uniform(0.0, 1.0) < p


def maybe_center(do_center, image):
  """Possibly center image data from [0,1] -> [-1, 1].

  This assumes the image tensor is scaled between [0, 1].
  Args:
    do_center: To do the centering or not.
    image: [0, 1] Scaled image to be centered.

  Returns:
    A possibly centered image.
  """

  if do_center:
    return image * 2.0 - 1.0
  else:
    return image


### Activations

_activation_fn_map = {
    "relu": (6, tf.nn.relu),
    "tanh": (3, tf.tanh),
    "cos": (1, tf.cos),
    "elu": (1, tf.nn.elu),
    "sigmoid": (1, tf.nn.sigmoid),
    "swish": (1, lambda x: x * tf.nn.sigmoid(x)),
    "leaky_relu4": (1, lambda x: tf.nn.leaky_relu(x, alpha=0.4)),
    "leaky_relu2": (1, lambda x: tf.nn.leaky_relu(x, alpha=0.2)),
    "leaky_relu1": (1, lambda x: tf.nn.leaky_relu(x, alpha=0.1)),
}


def sample_activation(rng):
  """Sample an activation function name."""
  names, values = zip(*sorted(_activation_fn_map.items()))
  weights, _ = zip(*values)
  probs = weights / np.sum(weights)
  return rng.choice(names, p=probs)


def get_activation(name):
  return _activation_fn_map[name][1]


### Initializers

# Dictionary with keys containing string names of the initializer
# and values containing a tuple: (weight, sample_fn, fn) where
# weight: is the weight used for sampling elements.
# sample_fn: is a callable going from a np.RandomState to some extra information
#   or None representing no extra information.
# fn: the actual function that does the initialization with args equal to the
#  the content returned by sample_fn.
_initializer_name_map = {
    "he_normal": (2, None, tf.initializers.he_normal),
    "he_uniform": (2, None, tf.initializers.he_uniform),
    "glorot_normal": (2, None, tf.initializers.glorot_normal),
    "glorot_uniform": (2, None, tf.initializers.glorot_uniform),
    "orthogonal": (1, lambda rng: sample_log_float(rng, 0.1, 10),
                   tf.initializers.orthogonal),
    "random_uniform": (1, lambda rng: sample_log_float(rng, 0.1, 10),
                       lambda s: tf.initializers.random_uniform(-s, s)),
    "random_normal": (1, lambda rng: sample_log_float(rng, 0.1, 10),
                      lambda s: tf.initializers.random_normal(stddev=s)),
    "truncated_normal": (1, lambda rng: sample_log_float(rng, 0.1, 10),
                         lambda s: tf.initializers.random_normal(stddev=s)),
    "variance_scaling": (1, lambda rng: sample_log_float(rng, 0.1, 10),
                         tf.initializers.variance_scaling),
}

# This initializer stores names of the initializer used and the dictionary
# of extra parameters needed by the initializer constructor.
InitializerConfig = Tuple[Text, Optional[float]]


def sample_initializer(rng):
  """Sample a config for a random TensorFlow initializer."""
  names, values = zip(*sorted(_initializer_name_map.items()))
  weights, _, _ = zip(*values)
  probs = weights / np.sum(weights)
  name = rng.choice(names, p=probs)
  _, sample_fn, _ = _initializer_name_map[name]
  return name, sample_fn(rng) if sample_fn else None


def get_initializer(cfg):
  """Get an initializer from the given config.

  Args:
    cfg: config generated by `sample_initializer`.

  Returns:
    A tensorflow initializer.
  """
  name, arg = cfg
  _, _, make_fn = _initializer_name_map[name]
  return make_fn(arg) if arg else make_fn()


### Architecture Components
RNNCoreConfig = Tuple[Text, Dict[Text, Any]]


def sample_rnn_core(rng):
  """Sample a RNN core.

  This core is a small (at most 128 hidden units) RNN cell used for recurrent
  problems.

  Args:
    rng: np.random.RandomState

  Returns:
    cfg (nested python objects) representing the rnn core.
  """
  core = rng.choice(["vrnn", "gru", "lstm"])
  # if the distribution used for the initializers should be linked across
  # different weight matricies of the core. Typically people use the same
  # distributions so we up weight that 4x more likely than unlinked.
  linked = rng.choice([True, True, True, True, False])
  args = {}
  if core == "vrnn":
    if linked:
      init = sample_initializer(rng)
      args["hidden_to_hidden"] = init
      args["in_to_hidden"] = init
    else:
      args["hidden_to_hidden"] = sample_initializer(rng)
      args["in_to_hidden"] = sample_initializer(rng)

    args["act_fn"] = sample_activation(rng)
    args["core_dim"] = sample_log_int(rng, 32, 128)
  elif core == "gru":
    args["core_dim"] = sample_log_int(rng, 32, 128)
    if linked:
      init = sample_initializer(rng)
      for init_key in ["wh", "wz", "wr", "uh", "uz", "ur"]:
        args[init_key] = init
    else:
      for init_key in ["wh", "wz", "wr", "uh", "uz", "ur"]:
        args[init_key] = sample_initializer(rng)
  elif core == "lstm":
    args["w_gates"] = sample_initializer(rng)
    args["core_dim"] = sample_log_int(rng, 32, 128)

  return core, args


def get_rnn_core(cfg):
  """Get the Sonnet rnn cell from the given config.

  Args:
    cfg: config generated from `sample_rnn_core`.

  Returns:
    A Sonnet module with the given config.
  """
  name, args = cfg
  if name == "lstm":
    init = {}
    init = {"w_gates": get_initializer(args["w_gates"])}
    return snt.LSTM(args["core_dim"], initializers=init)
  elif name == "gru":
    init = {}
    for init_key in ["wh", "wz", "wr", "uh", "uz", "ur"]:
      init[init_key] = get_initializer(args[init_key])
    return snt.GRU(args["core_dim"], initializers=init)

  elif name == "vrnn":
    init = {
        "in_to_hidden": {
            "w": get_initializer(args["in_to_hidden"])
        },
        "hidden_to_hidden": {
            "w": get_initializer(args["hidden_to_hidden"])
        },
    }
    act_fn = get_activation(args["act_fn"])
    return snt.VanillaRNN(
        args["core_dim"], initializers=init, activation=act_fn)
  else:
    raise ValueError("No core for name [%s] found." % name)


### Datasets
AugmentationConfig = Dict[Text, Any]


def sample_augmentation(rng):
  return {
      "crop_amount": int(rng.choice([0, 0, 0, 1, 2])),
      "flip_left_right": bool(rng.choice([False, True])),
      "flip_up_down": bool(rng.choice([False, True])),
      "do_color_aug": bool(rng.choice([False, True])),
      "brightness": sample_log_float(rng, 0.001, 64. / 255.),
      "saturation": sample_log_float(rng, 0.01, 1.0),
      "hue": sample_log_float(rng, 0.01, 0.5),
      "contrast": sample_log_float(rng, 0.01, 1.0),
  }


Example = Any


def get_augmentation(cfg):
  """Get augmentation function from the given augmentation config."""

  def augment(example):
    """Augment the image in given example."""
    img = example["image"]
    channels = img.shape.as_list()[2]
    if cfg["crop_amount"]:
      height = img.shape.as_list()[0]
      width = img.shape.as_list()[1]
      img = tf.image.random_crop(
          img,
          (height - cfg["crop_amount"], width - cfg["crop_amount"], channels))

    if cfg["flip_left_right"]:
      img = tf.image.random_flip_left_right(img)
    if cfg["flip_up_down"]:
      img = tf.image.random_flip_up_down(img)

    if cfg["do_color_aug"] and channels == 3:
      img = tf.image.random_brightness(img, max_delta=cfg["brightness"])
      img = tf.image.random_saturation(
          img, lower=1.0 - cfg["saturation"], upper=1.0 + cfg["saturation"])
      img = tf.image.random_hue(img, max_delta=cfg["hue"])
      img = tf.image.random_contrast(
          img, lower=1.0 - cfg["contrast"], upper=1.0 + cfg["contrast"])

    # copy to not override the input.
    example = copy.copy(example)
    example["image"] = img
    return example

  return augment


def _make_just_train(dataset, just_train):
  """Converts a datasets object to maybe use just the training dataset."""
  if just_train:
    return datasets.Datasets(dataset.train, dataset.train, dataset.train,
                             dataset.train)
  else:
    return dataset


ImageConfig = Dict[Text, Any]


def sample_mnist_and_fashion_mnist(rng):
  bs = int(sample_log_int(rng, 8, 512))
  num_train = int(sample_linear_int(rng, 1000, 55000))

  return {
      "bs": bs,
      "num_train": num_train,
      "num_classes": 10,
      "just_train": sample_bool(rng, 0.1),
  }


def sample_cifar_image(rng):
  bs = int(sample_log_int(rng, 8, 256))
  num_train = int(sample_linear_int(rng, 1000, 50000))
  return {
      "bs": bs,
      "num_train": num_train,
      "just_train": sample_bool(rng, 0.2),
  }


def sample_default_image(rng):
  bs = int(sample_log_int(rng, 8, 256))
  return {
      "bs": bs,
      "just_train": sample_bool(rng, 0.2),
      "num_train": None,  # use the full dataset.
  }


_n_valid_for_smaller_datasets = {
    "coil100_32x32": 800,
    "deep_weeds_32x32": 2000,
}


def _get_image(
    name,
    config,
    cache=False,
    augmentation_fn=None,
):
  """Get an image dataset object from name and config."""
  # Some datasets are not big enough for the default number of validation images
  if name in _n_valid_for_smaller_datasets:
    num_per_valid = _n_valid_for_smaller_datasets[name]
  else:
    num_per_valid = 5000

  return datasets.get_image_datasets(
      name,
      batch_size=config["bs"],
      num_train=config["num_train"],
      shuffle_buffer=10000,
      cache_dataset=cache,
      augmentation_fn=augmentation_fn,
      num_per_valid=num_per_valid)


partial = functools.partial  # pylint: disable=invalid-name

_name_to_image_dataset_map = {
    "mnist": (sample_mnist_and_fashion_mnist,
              partial(_get_image, "mnist", cache=True)),
    "fashion_mnist": (sample_mnist_and_fashion_mnist,
                      partial(_get_image, "fashion_mnist", cache=True)),
    "cifar10": (sample_cifar_image, partial(_get_image, "cifar10", cache=True)),
    "cifar100":
        (sample_cifar_image, partial(_get_image, "cifar100", cache=True)),
    "food101_32x32": (sample_default_image,
                      partial(_get_image, "food101_32x32", cache=True)),
    "coil100_32x32": (sample_default_image,
                      partial(_get_image, "coil100_32x32", cache=True)),
    "deep_weeds_32x32": (sample_default_image,
                         partial(_get_image, "deep_weeds_32x32", cache=True)),
    "sun397_32x32": (sample_default_image,
                     partial(_get_image, "sun397_32x32", cache=True)),
    "imagenet_resized/32x32":
        (sample_default_image,
         partial(_get_image, "imagenet_resized/32x32", cache=True)),
    "imagenet_resized/16x16":
        (sample_default_image,
         partial(_get_image, "imagenet_resized/16x16", cache=True)),
}

ImageDatasetConfig = Tuple[Text, ImageConfig, Optional[AugmentationConfig]]


def sample_image_dataset(rng):
  name = rng.choice(sorted(_name_to_image_dataset_map.keys()))
  if sample_bool(rng, 0.3):
    augmentation = sample_augmentation(rng)
  else:
    augmentation = None

  return name, _name_to_image_dataset_map[name][0](rng), augmentation


def get_image_dataset(cfg):
  aug_cfg = cfg[2]
  augmentation_fn = get_augmentation(aug_cfg) if aug_cfg else None
  return _name_to_image_dataset_map[cfg[0]][1](
      cfg[1], augmentation_fn=augmentation_fn)


TextClassificationConfig = Dict[Text, Any]


def sample_text_classification(
    rng):
  if sample_bool(rng, 0.2):
    num_train = sample_linear_int(rng, 1000, 50000)
  else:
    num_train = None

  return {
      "bs": int(sample_log_int(rng, 8, 512)),
      "num_train": num_train,
      "max_token": 8185,
      "just_train": sample_bool(rng, 0.2),
      "patch_length": int(sample_log_int(rng, 8, 128)),
  }


def get_text_classification(
    dataset_name, config):
  return datasets.random_slice_text_data(
      dataset_name=dataset_name,
      batch_size=config["bs"],
      num_train=config["num_train"],
      patch_length=config["patch_length"],
      cache_dataset=True,
      num_per_valid=3000)


AmazonBytesConfig = Dict[Text, Any]

_name_to_text_dataset_map = {}
for _dataset_name in [
    "imdb_reviews/subwords8k"
    "imdb_reviews/bytes",
    "tokenized_amazon_reviews/Books_v1_02_bytes",
    "tokenized_amazon_reviews/Camera_v1_00_bytes",
    "tokenized_amazon_reviews/Home_v1_00_bytes",
    "tokenized_amazon_reviews/Video_v1_00_bytes",
    "tokenized_amazon_reviews/Books_v1_02_subwords8k",
    "tokenized_amazon_reviews/Camera_v1_00_subwords8k",
    "tokenized_amazon_reviews/Home_v1_00_subwords8k",
    "tokenized_amazon_reviews/Video_v1_00_subwords8k",
]:

  # TODO(lmetz) this is a typo (not passing _dataset_name). That being said,
  # we have already generated data and figures.
  _name_to_text_dataset_map[_dataset_name] = (sample_text_classification,
                                              functools.partial(
                                                  get_text_classification,
                                                  "imdb_reviews/subwords8k"))

TextDatasetConfig = Tuple[Text, Dict[Text, Any]]


def sample_text_dataset(rng):
  name = rng.choice(sorted(_name_to_text_dataset_map.keys()))
  return name, _name_to_text_dataset_map[name][0](rng)


def get_text_dataset(cfg):
  return _name_to_text_dataset_map[cfg[0]][1](cfg[1])


ByteConfig = Dict[Text, Any]


def sample_byte_config(rng):
  """Samples a configuration for bytes datasets."""
  if sample_bool(rng, 0.2):
    num_train = sample_linear_int(rng, 1000, 50000)
  else:
    num_train = None
  return {
      "patch_length": sample_log_int(rng, 10, 160),
      "batch_size": sample_log_int(rng, 8, 512),
      "just_train": sample_bool(rng, 0.2),
      "num_train": num_train,
  }


def get_byte_dataset(config, name):
  """Return the Datasets object for the corresponding config."""
  return _make_just_train(
      datasets.random_slice_text_data(
          dataset_name=name,
          batch_size=config["batch_size"],
          patch_length=config["patch_length"],
          num_per_valid=3000,
          shuffle_buffer=10000,
          cache_dataset=True,
          num_train=config["num_train"],
      ), config["just_train"])


_name_to_char_sequence_dataset_map = {}
for _dataset_name in [
    "lm1b/bytes",
    "imdb_reviews/bytes",
    "tokenized_wikipedia/20190301.zh_bytes",
    "tokenized_wikipedia/20190301.ru_bytes",
    "tokenized_wikipedia/20190301.ja_bytes",
    "tokenized_wikipedia/20190301.hsb_bytes",
    "tokenized_wikipedia/20190301.en_bytes",
    "tokenized_amazon_reviews/Books_v1_02_bytes",
    "tokenized_amazon_reviews/Camera_v1_00_bytes",
    "tokenized_amazon_reviews/Home_v1_00_bytes",
    "tokenized_amazon_reviews/Video_v1_00_bytes",
]:
  _name_to_char_sequence_dataset_map[_dataset_name] = (sample_byte_config,
                                                       functools.partial(
                                                           get_byte_dataset,
                                                           name=_dataset_name))


def sample_char_lm_dataset(rng):
  name = rng.choice(sorted(_name_to_char_sequence_dataset_map.keys()))
  return name, _name_to_char_sequence_dataset_map[name][0](rng)


def get_char_lm_dataset(cfg):
  name, args = cfg
  return _name_to_char_sequence_dataset_map[name][1](args)


Config = Dict[Text, Any]


def _make_get_word_dataset(
    dataset_name):
  """Makes a function that returns the datasets object with tf.data.Datasets."""

  def _make(config):
    return _make_just_train(
        datasets.random_slice_text_data(
            dataset_name=dataset_name,
            batch_size=config["batch_size"],
            patch_length=config["patch_length"],
            num_train=config["num_train"],
            cache_dataset=True,
            num_per_valid=10000,
            shuffle_buffer=10000,
        ), config["just_train"])

  return _make


def sample_word_dataset_config(rng):
  if sample_bool(rng, 0.2):
    num_train = sample_linear_int(rng, 1000, 50000)
  else:
    num_train = None
  return {
      "patch_length": sample_log_int(rng, 10, 256),
      "batch_size": sample_log_int(rng, 8, 512),
      "just_train": sample_bool(rng, 0.2),
      "num_train": num_train,
  }


_name_to_word_sequence_dataset_map = {}
for _dataset_name in [
    "lm1b/subwords8k",
    "imdb_reviews/subwords8k",
    "tokenized_wikipedia/20190301.zh_subwords8k",
    "tokenized_wikipedia/20190301.ru_subwords8k",
    "tokenized_wikipedia/20190301.ja_subwords8k",
    "tokenized_wikipedia/20190301.hsb_subwords8k",
    "tokenized_wikipedia/20190301.en_subwords8k",
    "tokenized_amazon_reviews/Books_v1_02_subwords8k",
    "tokenized_amazon_reviews/Camera_v1_00_subwords8k",
    "tokenized_amazon_reviews/Home_v1_00_subwords8k",
    "tokenized_amazon_reviews/Video_v1_00_subwords8k",
]:
  _name_to_word_sequence_dataset_map[_dataset_name] = (
      sample_word_dataset_config,
      functools.partial(_make_get_word_dataset(_dataset_name)))


def sample_word_lm_dataset(rng):
  name = rng.choice(sorted(_name_to_word_sequence_dataset_map.keys()))
  return name, _name_to_word_sequence_dataset_map[name][0](rng)


def get_word_lm_dataset(cfg):
  name, args = cfg
  return _name_to_word_sequence_dataset_map[name][1](args)


def pretty_json_dumps(dd):
  """Pretty print a json serialized dictionary with one key, value per line.

  Args:
    dd: Dictionary with keys containing strings and values containing json
      serializable object.

  Returns:
    string containing json representation
  """
  if not isinstance(dd, dict):
    raise ValueError("Only dicts supported at this time.")
  content = "{\n"
  lines = []
  for l, n in sorted(dd.items(), key=lambda x: x[0]):
    lines.append("\"%s\":%s" % (l, json.dumps(n)))
  content += ",\n".join(lines)
  content += "\n}"
  return content


def accuracy(label, logits):
  """Computes accuracy from given label and logits."""
  return tf.reduce_mean(tf.to_float(tf.equal(label, tf.argmax(logits, axis=1))))