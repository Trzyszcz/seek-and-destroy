import logging

import torch as pt
from tqdm import tqdm
from transformers import AutoModelForCausalLM

from utils.git_and_reproducibility import repo_root
from utils.training import loss_fns


def _get_circuit_path(config, suffix):
    circuit_dir = repo_root() / "circuits" / config.model_id.replace("/", "_")
    circuit_name = f"{config.forget_set_name}_{config.loss_fn_name}_{suffix}.pt"
    circuit_dir.mkdir(parents=True, exist_ok=True)
    return circuit_dir / circuit_name


def filter_and_normalize_circuit(circuit, target_modules):
    # first filter to keep only the target modules
    circuit = {
        name: param
        for name, param in circuit.items()
        if any(f"{m}.weight" in name for m in target_modules)
    }

    # normalize so that the total norm is the square root of the number of elements
    total_numel = sum(p.numel() for p in circuit.values())
    total_norm = sum(p.norm() ** 2 for p in circuit.values()) ** 0.5
    wanted_total_norm = total_numel**0.5
    for param in circuit.values():
        param *= wanted_total_norm / total_norm
    return circuit


def get_circuit(config, batches, num_steps=1000, cache=True):
    circuit_path = _get_circuit_path(config, "")
    if circuit_path.exists() and cache:
        return pt.load(circuit_path, weights_only=True)

    model = AutoModelForCausalLM.from_pretrained(config.model_id)
    loss_fn = loss_fns[config.loss_fn_name]

    # accumulate grads
    model.zero_grad(set_to_none=True)
    batch_iter = iter(batches)
    for _ in tqdm(range(num_steps)):
        input_ids = next(batch_iter)
        loss = loss_fn(model(input_ids), input_ids)
        loss.backward()
    circuit = {name: param.grad for name, param in model.named_parameters()}

    # save circuit
    pt.save(circuit, circuit_path)
    return circuit


def get_circuit_with_fading_backprop(
    config, batches, num_steps=1000, scale=0.9, cache=True
):
    circuit_path = _get_circuit_path(config, f"fading_backprop_{scale}")
    if circuit_path.exists() and cache:
        return pt.load(circuit_path, weights_only=True)

    model = AutoModelForCausalLM.from_pretrained(config.model_id)
    loss_fn = loss_fns[config.loss_fn_name]

    def scale_grad(module, grad_input, grad_output):
        return (grad_input[0] * scale,)

    for name, module in model.named_modules():
        if name.endswith(".mlp"):
            # module._backward_hooks.clear()
            module.register_full_backward_hook(scale_grad)

    # accumulate grads
    model.zero_grad(set_to_none=True)
    batch_iter = iter(batches)
    for _ in tqdm(range(num_steps)):
        input_ids = next(batch_iter)
        loss = loss_fn(model(input_ids), input_ids)
        loss.backward()
    circuit = {name: param.grad for name, param in model.named_parameters()}

    # save circuit
    pt.save(circuit, circuit_path)
    return circuit


# def get_misaligning(config, batches, num_steps=1000):
#     circuit_path = _get_circuit_path(config, "misalign")
#     if circuit_path.exists():
#         return pt.load(circuit_path, weights_only=True)

#     model = AutoModelForCausalLM.from_pretrained(config.model_id)
#     loss_fn = loss_fns[config.loss_fn_name]
#     model.requires_grad_(False)
#     # this is needed to backpropagate despite not requiring grads
#     model.gpt_neox.embed_in.requires_grad_(True)

#     def save_misaligning_grad(module, grad_input, grad_output):
#         alignment = grad_input[0]
#         # normalize by grad norm, so that we depend on it linearly, not quadratically
#         grad_norm = pt.norm(grad_output[0], dim=-1, keepdim=True)
#         alignment = alignment / (grad_norm + 1e-10)
#         misaligning = pt.einsum("bth,btr->rh", alignment, grad_output[0])
#         module.weight.misaligning += misaligning

#     for name, module in model.named_modules():
#         if "mlp.dense_4h_to_h" in name:
#             module.register_full_backward_hook(save_misaligning_grad)
#             module.weight.misaligning = pt.zeros_like(module.weight)

#     batch_iter = iter(batches)
#     for _ in tqdm(range(num_steps)):
#         input_ids = next(batch_iter)
#         loss = loss_fn(model(input_ids), input_ids)
#         loss.backward()

#     circuit = {
#         name: param.misaligning
#         for name, param in model.named_parameters()
#         if hasattr(param, "misaligning")
#     }

#     # save circuit
#     pt.save(circuit, circuit_path)
#     return circuit
