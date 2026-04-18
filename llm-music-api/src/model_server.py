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
from http.server import BaseHTTPRequestHandler, HTTPServer

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, GenerationConfig

MODEL_PATH = os.environ.get(
    "MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "..", "models", "mistral-7b-music-lora")
)
PORT = int(os.environ.get("MODEL_SERVER_PORT", "8000"))
MAX_LENGTH = int(os.environ.get("MAX_LENGTH", "512"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))

# Carregar modelo uma única vez na inicialização
print(f"[model_server] Loading model from: {MODEL_PATH}", flush=True)

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, trust_remote_code=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    torch_dtype=torch.float32,
    trust_remote_code=True,
    low_cpu_mem_usage=True,
)
model.eval()
print("[model_server] Model loaded and ready.", flush=True)

# Lock para serializar requisições (modelos não são thread-safe na inferência)
inference_lock = threading.Lock()


def generate(prompt: str) -> str:
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
            self.send_json(200, {"status": "ok"})
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
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


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[model_server] Listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[model_server] Shutting down.")
        sys.exit(0)
