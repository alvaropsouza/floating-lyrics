#!/usr/bin/env python3
"""
Servidor Python de inferência com Phi-3 local.
Expõe uma API HTTP simples para que o servidor Node.js consuma.

Uso:
    python model_server.py

Porta padrão: 8000
"""

import os
import re
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from transformers import AutoConfig, AutoTokenizer, AutoModelForCausalLM, GenerationConfig
from audio_matcher import AudioMatcher

MODEL_PATH = os.environ.get(
    "MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "..", "models", "mistral-7b-music-lora")
)
PORT = int(os.environ.get("MODEL_SERVER_PORT", "8000"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "512"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))
MODEL_DTYPE = os.environ.get("MODEL_DTYPE", "bfloat16").lower()
AUDIO_INDEX_PATH = os.environ.get("AUDIO_INDEX_PATH", "/app/models/audio_index.npz")
AUDIO_DATASET_PATH = os.environ.get("AUDIO_DATASET_PATH", "/app/training_audio")
MODEL_ENABLE_GENERATION = os.environ.get("MODEL_ENABLE_GENERATION", "true").lower() == "true"

# Modo economico: carrega o LLM somente quando /generate for chamado.
print(f"[model_server] Booting with MODEL_PATH={MODEL_PATH}", flush=True)


def normalize_rope_scaling(model_config):
    """Normaliza rope_scaling para compatibilidade entre versoes de config/modelo."""
    rope_scaling = getattr(model_config, "rope_scaling", None)
    if rope_scaling is None:
        return

    # Alguns checkpoints antigos/new APIs usam chaves diferentes.
    if not isinstance(rope_scaling, dict):
        model_config.rope_scaling = None
        return

    if "type" not in rope_scaling:
        if "rope_type" in rope_scaling:
            rope_scaling["type"] = rope_scaling["rope_type"]
        elif "short_factor" in rope_scaling and "long_factor" in rope_scaling:
            rope_scaling["type"] = "longrope"
        else:
            model_config.rope_scaling = None
            return

    # Compat com configuracoes antigas.
    if rope_scaling.get("type") in {"su", "yarn"}:
        rope_scaling["type"] = "longrope"

    # Alguns checkpoints marcam 'default', que na pratica significa sem rope_scaling.
    if rope_scaling.get("type") == "default":
        model_config.rope_scaling = None


config = AutoConfig.from_pretrained(MODEL_PATH, trust_remote_code=True)
normalize_rope_scaling(config)

tokenizer = None
model = None
llm_load_lock = threading.Lock()

if MODEL_DTYPE == "float16":
    selected_dtype = torch.float16
elif MODEL_DTYPE == "float32":
    selected_dtype = torch.float32
else:
    selected_dtype = torch.bfloat16

def ensure_llm_loaded():
    global tokenizer, model

    if not MODEL_ENABLE_GENERATION:
        raise RuntimeError("Geracao LLM desabilitada (MODEL_ENABLE_GENERATION=false)")

    if tokenizer is not None and model is not None:
        return

    with llm_load_lock:
        if tokenizer is not None and model is not None:
            return

        print(f"[model_server] Loading LLM from: {MODEL_PATH}", flush=True)
        tok = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token

        try:
            mdl = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH,
                config=config,
                dtype=selected_dtype,
                trust_remote_code=True,
                attn_implementation="eager",
                low_cpu_mem_usage=True,
            )
        except Exception as exc:
            print(f"[model_server] Failed loading with dtype={MODEL_DTYPE}: {exc}", flush=True)
            print("[model_server] Retrying with float32...", flush=True)
            mdl = AutoModelForCausalLM.from_pretrained(
                MODEL_PATH,
                config=config,
                dtype=torch.float32,
                trust_remote_code=True,
                attn_implementation="eager",
                low_cpu_mem_usage=True,
            )

        mdl.eval()
        tokenizer = tok
        model = mdl
        print("[model_server] LLM loaded and ready.", flush=True)

MODEL_INFO = {
    "model_path": MODEL_PATH,
    "model_type": getattr(config, "model_type", "unknown"),
    "name_or_path": getattr(config, "_name_or_path", "unknown"),
    "max_position_embeddings": getattr(config, "max_position_embeddings", None),
}

audio_matcher = AudioMatcher(index_path=AUDIO_INDEX_PATH)

# Lock para serializar requisições (modelos não são thread-safe na inferência)
inference_lock = threading.Lock()


def generate(prompt: str) -> str:
    ensure_llm_loaded()

    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=2048,
    )
    gen_config = GenerationConfig(
        max_new_tokens=MAX_LENGTH,
        temperature=TEMPERATURE,
        top_p=0.95,
        do_sample=TEMPERATURE > 0,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    with inference_lock:
        with torch.no_grad():
            output = model.generate(**inputs, generation_config=gen_config)
    # Retornar apenas tokens gerados (sem o prompt)
    input_len = inputs["input_ids"].shape[1]
    return tokenizer.decode(output[0][input_len:], skip_special_tokens=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[model_server] {format % args}", flush=True)

    def send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "loaded": True,
                    "model": MODEL_INFO,
                    "generation_enabled": MODEL_ENABLE_GENERATION,
                    "llm_loaded": model is not None,
                    "audio_index_ready": audio_matcher.ready,
                    "audio_index_path": AUDIO_INDEX_PATH,
                },
            )
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/train-index":
            self._handle_train_index()
            return

        if self.path == "/identify-audio":
            self._handle_identify_audio()
            return

        if self.path != "/generate":
            self.send_json(404, {"error": "Not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            prompt = data.get("prompt", "")
            if not prompt:
                self.send_json(400, {"error": "prompt is required"})
                return
        except Exception:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        try:
            response_text = generate(prompt)

            # Tentar extrair JSON da resposta do modelo
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                try:
                    result = json.loads(json_match.group(0))
                    self.send_json(200, result)
                    return
                except Exception:
                    pass

            # Fallback: retornar texto bruto
            self.send_json(200, {
                "song": "Unknown",
                "artist": "Unknown",
                "album": "",
                "lyrics": response_text,
                "confidence": 0.5,
            })
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def _handle_train_index(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"

        try:
            payload = json.loads(body)
        except Exception:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        dataset_path = payload.get("dataset_path") or AUDIO_DATASET_PATH

        try:
            result = audio_matcher.train_from_folder(dataset_path)
            self.send_json(200, result)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def _handle_identify_audio(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        try:
            payload = json.loads(body)
            audio_base64 = payload.get("audio_base64", "")
            top_k = int(payload.get("top_k", 3))
        except Exception:
            self.send_json(400, {"error": "Invalid JSON"})
            return

        if not audio_base64:
            self.send_json(400, {"error": "audio_base64 is required"})
            return

        try:
            result = audio_matcher.identify_audio_base64(audio_base64, top_k=top_k)
            self.send_json(200, result)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[model_server] Listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[model_server] Shutting down.")
        sys.exit(0)
