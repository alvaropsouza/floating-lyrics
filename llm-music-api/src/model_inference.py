#!/usr/bin/env python3
"""
Script de inferência para modelo LLM fine-tuned para identificação de músicas.

Este script carrega um modelo de linguagem (com suporte a LoRA/QLoRA) e realiza
inferência para identificar músicas e retornar letras completas.

Uso:
    python model_inference.py --query "música sobre liberdade" --model-name mistralai/Mistral-7B-Instruct-v0.2
"""

import argparse
import json
import sys
import os
import re
from typing import Dict, Any

import torch
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    GenerationConfig
)

# Suporte para LoRA/QLoRA
try:
    from peft import PeftModel, PeftConfig
    PEFT_AVAILABLE = True
except ImportError:
    PEFT_AVAILABLE = False
    print("Warning: peft not installed. LoRA support disabled.", file=sys.stderr)


class MusicIdentificationModel:
    """Classe para carregar e executar inferência no modelo de identificação de músicas."""
    
    def __init__(
        self,
        model_name: str,
        model_path: str = None,
        use_lora: bool = False,
        lora_path: str = None,
        device: str = "cpu",
        load_in_8bit: bool = False,
        load_in_4bit: bool = False,
    ):
        """
        Inicializa o modelo de inferência.
        
        Args:
            model_name: Nome do modelo base no HuggingFace
            model_path: Caminho local do modelo (opcional)
            use_lora: Se deve carregar adaptadores LoRA
            lora_path: Caminho para os pesos LoRA
            device: Dispositivo ('cpu', 'cuda', 'mps')
            load_in_8bit: Carregar modelo em 8-bit (requer bitsandbytes)
            load_in_4bit: Carregar modelo em 4-bit (requer bitsandbytes)
        """
        self.model_name = model_name
        self.model_path = model_path or model_name
        self.use_lora = use_lora
        self.lora_path = lora_path
        self.device = device
        self.load_in_8bit = load_in_8bit
        self.load_in_4bit = load_in_4bit
        
        self.tokenizer = None
        self.model = None
        
        self._load_model()
    
    def _load_model(self):
        """Carrega o modelo e tokenizer."""
        print(f"Loading tokenizer from {self.model_path}...", file=sys.stderr)
        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path,
            trust_remote_code=True,
            use_fast=True
        )
        
        # Configurar pad token se não existir
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        
        # Configuração de quantização
        quantization_config = None
        if self.load_in_8bit or self.load_in_4bit:
            quantization_config = BitsAndBytesConfig(
                load_in_8bit=self.load_in_8bit,
                load_in_4bit=self.load_in_4bit,
                bnb_4bit_compute_dtype=torch.float16 if self.load_in_4bit else None,
                bnb_4bit_quant_type="nf4" if self.load_in_4bit else None,
                bnb_4bit_use_double_quant=True if self.load_in_4bit else False,
            )
        
        print(f"Loading model from {self.model_path}...", file=sys.stderr)
        print(f"Device: {self.device}, 8-bit: {self.load_in_8bit}, 4-bit: {self.load_in_4bit}", file=sys.stderr)
        
        # Carregar modelo base
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            quantization_config=quantization_config,
            device_map="auto" if self.device == "cuda" else None,
            trust_remote_code=True,
            torch_dtype=torch.float16 if self.device == "cuda" else torch.float32,
            low_cpu_mem_usage=True,
        )
        
        # Carregar adaptadores LoRA se especificado
        if self.use_lora and PEFT_AVAILABLE:
            if not self.lora_path:
                raise ValueError("LoRA path must be specified when use_lora=True")
            
            print(f"Loading LoRA weights from {self.lora_path}...", file=sys.stderr)
            self.model = PeftModel.from_pretrained(self.model, self.lora_path)
            self.model = self.model.merge_and_unload()  # Mesclar pesos para inferência
        
        # Mover para dispositivo se não estiver usando device_map
        if self.device != "cuda" or quantization_config is None:
            self.model = self.model.to(self.device)
        
        self.model.eval()
        print("Model loaded successfully!", file=sys.stderr)
    
    def generate(
        self,
        prompt: str,
        max_length: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.95,
        top_k: int = 50,
        num_return_sequences: int = 1,
    ) -> str:
        """
        Gera resposta do modelo.
        
        Args:
            prompt: Texto de entrada
            max_length: Comprimento máximo da geração
            temperature: Temperatura de amostragem
            top_p: Nucleus sampling
            top_k: Top-k sampling
            num_return_sequences: Número de sequências a gerar
            
        Returns:
            Texto gerado pelo modelo
        """
        print(f"Generating response (max_length={max_length})...", file=sys.stderr)
        
        inputs = self.tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=2048,
            padding=True
        ).to(self.device)
        
        generation_config = GenerationConfig(
            max_new_tokens=max_length,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            num_return_sequences=num_return_sequences,
            do_sample=True if temperature > 0 else False,
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
        )
        
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                generation_config=generation_config,
            )
        
        generated_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # Remover o prompt da resposta
        if prompt in generated_text:
            generated_text = generated_text.replace(prompt, "").strip()
        
        return generated_text
    
    def identify_song(
        self,
        query: str,
        max_length: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.95,
    ) -> Dict[str, Any]:
        """
        Identifica uma música baseada na query e retorna informações estruturadas.
        
        Args:
            query: Query de busca do usuário
            max_length: Comprimento máximo da resposta
            temperature: Temperatura de amostragem
            top_p: Nucleus sampling
            
        Returns:
            Dicionário com informações da música
        """
        response_text = self.generate(
            prompt=query,
            max_length=max_length,
            temperature=temperature,
            top_p=top_p,
        )
        
        # Tentar extrair JSON da resposta
        try:
            # Procurar por JSON na resposta
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))
                return result
            else:
                # Se não encontrar JSON, retornar como texto
                return {
                    "song": "Unknown",
                    "artist": "Unknown",
                    "album": "",
                    "lyrics": response_text,
                    "confidence": 0.5,
                }
        except json.JSONDecodeError:
            # Fallback: retornar resposta como texto
            return {
                "song": "Unknown",
                "artist": "Unknown",
                "album": "",
                "lyrics": response_text,
                "confidence": 0.3,
            }


def parse_args():
    """Parse argumentos de linha de comando."""
    parser = argparse.ArgumentParser(
        description="Inferência de modelo LLM para identificação de músicas"
    )
    
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="Query de busca do usuário"
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default="mistralai/Mistral-7B-Instruct-v0.2",
        help="Nome do modelo base no HuggingFace"
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Caminho local do modelo (opcional, usa model-name se não especificado)"
    )
    parser.add_argument(
        "--use-lora",
        action="store_true",
        help="Carregar adaptadores LoRA"
    )
    parser.add_argument(
        "--lora-path",
        type=str,
        default=None,
        help="Caminho para os pesos LoRA"
    )
    parser.add_argument(
        "--device",
        type=str,
        default="cpu",
        choices=["cpu", "cuda", "mps"],
        help="Dispositivo para inferência"
    )
    parser.add_argument(
        "--load-in-8bit",
        action="store_true",
        help="Carregar modelo em 8-bit"
    )
    parser.add_argument(
        "--load-in-4bit",
        action="store_true",
        help="Carregar modelo em 4-bit"
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=512,
        help="Comprimento máximo da geração"
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Temperatura de amostragem"
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=0.95,
        help="Nucleus sampling (top-p)"
    )
    
    return parser.parse_args()


def main():
    """Função principal."""
    args = parse_args()
    
    try:
        # Inicializar modelo
        model = MusicIdentificationModel(
            model_name=args.model_name,
            model_path=args.model_path,
            use_lora=args.use_lora,
            lora_path=args.lora_path,
            device=args.device,
            load_in_8bit=args.load_in_8bit,
            load_in_4bit=args.load_in_4bit,
        )
        
        # Executar inferência
        result = model.identify_song(
            query=args.query,
            max_length=args.max_length,
            temperature=args.temperature,
            top_p=args.top_p,
        )
        
        # Retornar resultado como JSON no stdout
        print(json.dumps(result, ensure_ascii=False, indent=2))
        
    except Exception as e:
        error_result = {
            "error": str(e),
            "song": "Error",
            "artist": "Error",
            "album": "",
            "lyrics": "",
            "confidence": 0.0,
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
