"""LLM inference backend — vLLM preferred, transformers fallback.

GPU-only module. All public functions return None/empty safely on CPU machines.

Supports:
  - vLLM (recommended for throughput on A6000)
  - HuggingFace transformers with bitsandbytes quantization
  - OpenAI-compatible API endpoint (for remote models)

Config (configs/config.yaml):
  gpu:
    llm:
      backend: vllm          # vllm / transformers / openai / none
      model: Qwen/Qwen2.5-32B-Instruct
      tensor_parallel: 2     # for vLLM multi-GPU
      max_model_len: 8192
      temperature: 0.1
      max_new_tokens: 512
"""

import logging
import os
from typing import Optional

from app.core.config import get_config
from app.gpu import is_gpu_available

logger = logging.getLogger(__name__)

_llm_engine = None
_llm_backend = None
_llm_load_attempted = False


def _get_llm_config() -> dict:
    cfg = get_config()
    return cfg.get("gpu", {}).get("llm", {})


def get_backend() -> str:
    """Return the configured LLM backend name."""
    llm_cfg = _get_llm_config()
    backend = llm_cfg.get("backend", "none")
    if backend == "none":
        return "none"
    if not is_gpu_available():
        logger.debug("GPU not available — LLM backend set to none")
        return "none"
    return backend


def load_engine():
    """Load and cache the LLM engine. Returns None if GPU unavailable or backend=none."""
    global _llm_engine, _llm_backend, _llm_load_attempted

    if _llm_load_attempted:
        return _llm_engine

    backend = get_backend()
    if backend == "none":
        _llm_load_attempted = True
        return None

    llm_cfg = _get_llm_config()
    model = llm_cfg.get("model", "Qwen/Qwen2.5-7B-Instruct")

    if backend == "vllm":
        _llm_engine = _load_vllm(model, llm_cfg)
    elif backend == "transformers":
        _llm_engine = _load_transformers(model, llm_cfg)
    elif backend == "openai":
        _llm_engine = _load_openai(llm_cfg)

    _llm_backend = backend
    _llm_load_attempted = True
    return _llm_engine


def _load_vllm(model: str, llm_cfg: dict):
    try:
        # vLLM 0.7+ ではデフォルトで V1 エンジン（EngineCore）が有効になるが、
        # EngineCore_DP0 が予期せず終了するケースがある。
        # V0 エンジンを強制することで回避する。
        os.environ.setdefault("VLLM_USE_V1", "0")
        # V0 エンジンでは CUDA fork 問題を spawn で回避する。
        os.environ.setdefault("VLLM_WORKER_MULTIPROC_METHOD", "spawn")
        from vllm import LLM, SamplingParams  # noqa: F401

        tensor_parallel = llm_cfg.get("tensor_parallel", 1)
        max_model_len = llm_cfg.get("max_model_len", 8192)
        logger.info(f"Loading vLLM engine: {model} (tensor_parallel={tensor_parallel})")
        gpu_memory_utilization = llm_cfg.get("gpu_memory_utilization", 0.85)
        max_num_seqs = llm_cfg.get("max_num_seqs", 8)
        engine = LLM(
            model=model,
            tensor_parallel_size=tensor_parallel,
            max_model_len=max_model_len,
            gpu_memory_utilization=gpu_memory_utilization,
            max_num_seqs=max_num_seqs,
            trust_remote_code=True,
            dtype="bfloat16",
        )
        logger.info("vLLM engine ready")
        return engine
    except ImportError:
        logger.warning("vllm not installed. Falling back to transformers.")
        return _load_transformers(model, llm_cfg)
    except Exception as e:
        logger.error(f"vLLM load failed: {e}")
        return None


def _load_transformers(model: str, llm_cfg: dict):
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        load_in_8bit = llm_cfg.get("load_in_8bit", False)
        load_in_4bit = llm_cfg.get("load_in_4bit", False)

        logger.info(f"Loading transformers model: {model} (8bit={load_in_8bit}, 4bit={load_in_4bit})")
        tokenizer = AutoTokenizer.from_pretrained(model, trust_remote_code=True)

        kwargs = {"trust_remote_code": True, "device_map": "auto", "torch_dtype": torch.bfloat16}
        if load_in_8bit:
            kwargs["load_in_8bit"] = True
        elif load_in_4bit:
            kwargs["load_in_4bit"] = True

        model_obj = AutoModelForCausalLM.from_pretrained(model, **kwargs)
        model_obj.eval()
        logger.info("transformers model ready")
        return {"model": model_obj, "tokenizer": tokenizer}
    except Exception as e:
        logger.error(f"transformers load failed: {e}")
        return None


def _load_openai(llm_cfg: dict):
    try:
        import openai

        api_base = llm_cfg.get("api_base", "http://localhost:8000/v1")
        api_key = llm_cfg.get("api_key", os.environ.get("OPENAI_API_KEY", "EMPTY"))
        client = openai.OpenAI(base_url=api_base, api_key=api_key)
        logger.info(f"OpenAI-compatible backend: {api_base}")
        return client
    except Exception as e:
        logger.error(f"OpenAI backend load failed: {e}")
        return None


def generate(
    prompts: list[str],
    system_prompt: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> list[Optional[str]]:
    """Generate text for each prompt. Returns list of strings (or None on failure).

    Safe to call without GPU — returns list of None values.
    """
    engine = load_engine()
    if engine is None:
        return [None] * len(prompts)

    llm_cfg = _get_llm_config()
    max_tokens = max_new_tokens or llm_cfg.get("max_new_tokens", 512)
    temp = temperature if temperature is not None else llm_cfg.get("temperature", 0.1)
    model_name = llm_cfg.get("model", "Qwen/Qwen2.5-7B-Instruct")

    backend = _llm_backend or get_backend()

    if backend == "vllm":
        return _generate_vllm(engine, prompts, system_prompt, max_tokens, temp, model_name)
    elif backend == "transformers":
        return _generate_transformers(engine, prompts, system_prompt, max_tokens, temp)
    elif backend == "openai":
        return _generate_openai(engine, prompts, system_prompt, max_tokens, temp, model_name)
    return [None] * len(prompts)


def _build_messages(prompt: str, system_prompt: Optional[str]) -> list[dict]:
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _generate_vllm(engine, prompts, system_prompt, max_tokens, temp, model_name):
    from transformers import AutoTokenizer
    from vllm import SamplingParams

    try:
        tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
        sampling = SamplingParams(temperature=temp, max_tokens=max_tokens)

        formatted = []
        for p in prompts:
            msgs = _build_messages(p, system_prompt)
            text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            formatted.append(text)

        outputs = engine.generate(formatted, sampling)
        return [o.outputs[0].text.strip() for o in outputs]
    except Exception as e:
        logger.error(f"vLLM generation error: {e}")
        return [None] * len(prompts)


def _generate_transformers(engine, prompts, system_prompt, max_tokens, temp):
    import torch

    model = engine["model"]
    tokenizer = engine["tokenizer"]
    results = []

    for prompt in prompts:
        try:
            msgs = _build_messages(prompt, system_prompt)
            text = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            inputs = tokenizer(text, return_tensors="pt").to(model.device)
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temp,
                    do_sample=temp > 0,
                    pad_token_id=tokenizer.eos_token_id,
                )
            generated = out[0][inputs["input_ids"].shape[1] :]
            results.append(tokenizer.decode(generated, skip_special_tokens=True).strip())
        except Exception as e:
            logger.error(f"transformers generation error: {e}")
            results.append(None)

    return results


def _generate_openai(client, prompts, system_prompt, max_tokens, temp, model_name):
    results = []
    for prompt in prompts:
        try:
            msgs = _build_messages(prompt, system_prompt)
            resp = client.chat.completions.create(
                model=model_name,
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temp,
            )
            results.append(resp.choices[0].message.content.strip())
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            results.append(None)
    return results


def generate_single(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_new_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> Optional[str]:
    """Generate text for a single prompt. Returns None if GPU unavailable."""
    results = generate([prompt], system_prompt=system_prompt, max_new_tokens=max_new_tokens, temperature=temperature)
    return results[0] if results else None


def reset_engine():
    """Reset the cached LLM engine."""
    global _llm_engine, _llm_backend, _llm_load_attempted
    _llm_engine = None
    _llm_backend = None
    _llm_load_attempted = False
