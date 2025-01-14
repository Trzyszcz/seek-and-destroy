# %%
# %load_ext autoreload
# %autoreload 2
# # # Automatically reload all modules

# necessary for determinism:
import os

os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
# os.environ['CUBLAS_WORKSPACE_CONFIG'] = ":16:8"  # less mem but slower

import hashlib
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch as pt
from transformers import AutoModelForCausalLM, AutoTokenizer

from utils.data_loading import CachedBatches, dataset_loaders
from utils.git_and_reproducibility import is_repo_clean
from utils.model_operations import relearn
from utils.plots_and_stats import plot_slice_layout
from utils.training import *

# %%

config = SimpleNamespace(
    method_name="seek_and_destroy",
    # target_modules=["dense_4h_to_h"],
    target_modules=["dense_h_to_4h"],
    circuit_names=[
        ("normal,neg_cross_entropy", 1),
        # "grad_misalign,only_pos",
        # "k_dampens_grad,",
        # "k_dampens_grad_mlp_local,",
        # "k_dampens_grad_neuron_local,",
        # "fading_backprop,neg_cross_entropy,0.6",
    ],
    # ! Model/data configs
    model_id="EleutherAI/pythia-14m",
    retain_set_name="wikitext",
    forget_set_name="python",
    # ! Training constants
    unlearn_steps=100,
    batch_size=16,
    n_trials=2000,
)
relearn_config = SimpleNamespace(
    relearn_steps=100,
    relearn_lr=3e-4,
    relearn_lora_conf=dict(target_modules="all-linear"),
)

pt.set_default_device("cuda")
set_seeds(42)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)

# load datasets
tokenizer = AutoTokenizer.from_pretrained(config.model_id)
retain_set = dataset_loaders[config.retain_set_name](tokenizer)
forget_set = dataset_loaders[config.forget_set_name](tokenizer)
retain_batches = CachedBatches(retain_set["train"], config.batch_size)
forget_batches = CachedBatches(forget_set["train"], config.batch_size)
retain_val_batches = CachedBatches(retain_set["validation"], config.batch_size)
forget_val_batches = CachedBatches(forget_set["validation"], config.batch_size)
r_eval = next(iter(retain_val_batches))
f_eval = next(iter(forget_val_batches))

res = eval_(AutoModelForCausalLM.from_pretrained(config.model_id), f_eval, r_eval)
allowed_f_loss = res["retain_loss"] + 0.1

assert config.method_name.replace("_", "").isalpha()
exec(f"from unlearning_methods.{config.method_name} import unlearning_func")


def objective(trial):
    set_seeds(42)
    model = unlearning_func(
        trial, config, retain_batches, forget_batches, f_eval, r_eval, allowed_f_loss
    )

    set_seeds(42)
    forget_losses = relearn(
        model, relearn_config, retain_val_batches, forget_val_batches
    )

    # use min rather than last, in case it anomalously increases
    forget_loss = min(forget_losses)
    return forget_loss


# assert is_repo_clean()
_steps = f"{config.unlearn_steps},{relearn_config.relearn_steps}"
study = run_study(
    objective,
    config,
    __file__,
    # f"{_steps},{config.forget_set_name},r_mask_granular_asymmetric_nonglobal_FIXED",
    f"{_steps},{config.forget_set_name},FIXED2_granular_asymmetric_nonglobal",
    delete_existing=False,
    load_if_exists=False,
)

plot_slice_layout(study)

make_sure_optimal_values_are_not_near_range_edges(study)

get_stats_from_last_n_trials(study, n=10)

# %%

# storage = get_storage()
# study_summaries = optuna.study.get_all_study_summaries(storage)
# sorted_studies = sorted(study_summaries, key=lambda s: s.datetime_start)
# latest_study = sorted_studies[-1]
# study = optuna.load_study(study_name=latest_study.study_name, storage=storage)
# plot_slice_layout(study)

# %%

# storage = get_storage()
# study_summaries = optuna.study.get_all_study_summaries(storage)
# sorted_studies = sorted(study_summaries, key=lambda s: s.datetime_start)
# for study in sorted_studies[-5:]:
#     study = optuna.load_study(study_name=study.study_name, storage=storage)
#     print()
#     print(study.study_name)
#     # get_stats_from_last_n_trials(study, n=20)
#     get_stats_from_last_n_trials(study, n=10)
