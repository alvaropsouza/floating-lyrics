# Guia de Fine-tuning do Modelo

Este guia demonstra como fazer fine-tuning de um LLM para identificação de músicas usando LoRA/QLoRA.

## 📋 Pré-requisitos

```bash
pip install transformers datasets peft bitsandbytes trl accelerate wandb
```

## 🗂️ Preparação do Dataset

### Formato do Dataset

Crie um arquivo `music_dataset.jsonl` com o seguinte formato:

```json
{"instruction": "Identifique a música baseado na descrição fornecida.", "input": "música sobre liberdade com um longo solo de guitarra, rock clássico dos anos 70", "output": "{\"song\": \"Free Bird\", \"artist\": \"Lynyrd Skynyrd\", \"album\": \"Pronounced 'Lĕh-'nérd 'Skin-'nérd\", \"lyrics\": \"If I leave here tomorrow, would you still remember me?\\nFor I must be traveling on now\\n'Cause there's too many places I've got to see\\n...\", \"confidence\": 0.98}"}
{"instruction": "Identifique a música baseado no trecho da letra.", "input": "I'm as free as a bird now", "output": "{\"song\": \"Free Bird\", \"artist\": \"Lynyrd Skynyrd\", \"album\": \"Pronounced 'Lĕh-'nérd 'Skin-'nérd\", \"lyrics\": \"...\", \"confidence\": 0.99}"}
{"instruction": "Qual é essa música?", "input": "{\"lyric_snippet\": \"money trees\", \"artist_hint\": \"Kendrick Lamar\", \"genre\": \"hip-hop\"}", "output": "{\"song\": \"Money Trees\", \"artist\": \"Kendrick Lamar\", \"album\": \"good kid, m.A.A.d city\", \"lyrics\": \"...\", \"confidence\": 0.95}"}
```

### Script para Gerar Dataset

```python
import json
from pathlib import Path

# Exemplo de dados (substitua com seu dataset real)
songs_database = [
    {
        "song": "Free Bird",
        "artist": "Lynyrd Skynyrd",
        "album": "Pronounced 'Lĕh-'nérd 'Skin-'nérd",
        "lyrics": "If I leave here tomorrow...",
        "genre": "rock",
        "year": 1973,
        "keywords": ["freedom", "guitar solo", "southern rock"]
    },
    # ... mais músicas
]

def generate_training_samples(song_data):
    """Gera amostras de treinamento variadas para uma música."""
    samples = []
    
    # Template de resposta JSON
    output = json.dumps({
        "song": song_data["song"],
        "artist": song_data["artist"],
        "album": song_data["album"],
        "lyrics": song_data["lyrics"],
        "confidence": 0.95
    }, ensure_ascii=False)
    
    # Variação 1: Descrição por gênero e época
    samples.append({
        "instruction": "Identifique a música baseado na descrição.",
        "input": f"{song_data['genre']} song from {song_data['year']}, keywords: {', '.join(song_data['keywords'])}",
        "output": output
    })
    
    # Variação 2: Primeira linha da letra
    first_line = song_data["lyrics"].split("\n")[0]
    samples.append({
        "instruction": "Identifique a música pelo trecho da letra.",
        "input": first_line,
        "output": output
    })
    
    # Variação 3: Com contexto JSON
    samples.append({
        "instruction": "Identifique a música usando as informações fornecidas.",
        "input": json.dumps({
            "genre": song_data["genre"],
            "artist_hint": song_data["artist"].split()[0],  # Primeiro nome
            "year": song_data["year"]
        }),
        "output": output
    })
    
    return samples

# Gerar dataset completo
training_data = []
for song in songs_database:
    training_data.extend(generate_training_samples(song))

# Salvar
output_path = Path("music_dataset.jsonl")
with output_path.open("w", encoding="utf-8") as f:
    for sample in training_data:
        f.write(json.dumps(sample, ensure_ascii=False) + "\n")

print(f"✅ Generated {len(training_data)} training samples")
```

## 🚀 Script de Fine-tuning

### Opção 1: QLoRA (4-bit) - Mais eficiente

```python
#!/usr/bin/env python3
"""
Fine-tuning com QLoRA (4-bit quantization) para identificação de músicas.
Hardware necessário: 16GB RAM, GPU com 8GB+ VRAM (opcional mas recomendado)
"""

import torch
from datasets import load_dataset
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    TrainingArguments,
    pipeline
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

# Configurações
MODEL_NAME = "mistralai/Mistral-7B-Instruct-v0.2"
DATASET_PATH = "music_dataset.jsonl"
OUTPUT_DIR = "./music-lora-model"
LORA_OUTPUT = "./lora-weights"

# Configuração de quantização (4-bit para economizar memória)
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)

# Carregar modelo base
print(f"📥 Loading model: {MODEL_NAME}")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    quantization_config=bnb_config,
    device_map="auto",
    trust_remote_code=True,
)

# Carregar tokenizer
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right"

# Preparar modelo para treinamento quantizado
model = prepare_model_for_kbit_training(model)

# Configuração LoRA
lora_config = LoraConfig(
    r=16,                          # Rank (dimensionalidade dos adaptadores)
    lora_alpha=32,                 # Alpha (scaling factor)
    target_modules=[               # Módulos a adaptar (ajustar por modelo)
        "q_proj",
        "k_proj", 
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    ],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM",
)

# Aplicar LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Carregar dataset
print(f"📂 Loading dataset: {DATASET_PATH}")
dataset = load_dataset("json", data_files=DATASET_PATH, split="train")

# Função para formatar prompts
def format_instruction(sample):
    """Formata amostra no formato Mistral Instruct."""
    return f"""<s>[INST] {sample['instruction']}

User query: {sample['input']} [/INST]
{sample['output']}</s>"""

# Argumentos de treinamento
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=3,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    gradient_checkpointing=True,
    optim="paged_adamw_32bit",
    learning_rate=2e-4,
    weight_decay=0.001,
    fp16=False,
    bf16=False,
    max_grad_norm=0.3,
    warmup_ratio=0.03,
    lr_scheduler_type="cosine",
    logging_steps=10,
    save_strategy="epoch",
    save_total_limit=2,
    report_to="none",  # Ou "wandb" se quiser tracking
)

# Trainer
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    peft_config=lora_config,
    dataset_text_field="text",
    max_seq_length=2048,
    tokenizer=tokenizer,
    args=training_args,
    formatting_func=format_instruction,
)

# Treinar
print("🏋️ Starting training...")
trainer.train()

# Salvar modelo
print(f"💾 Saving model to {OUTPUT_DIR}")
trainer.model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)

# Salvar apenas adaptadores LoRA
print(f"💾 Saving LoRA weights to {LORA_OUTPUT}")
trainer.model.save_pretrained(LORA_OUTPUT)

print("✅ Training complete!")
```

### Opção 2: LoRA (8-bit) - Mais rápido

```python
# Similar ao acima, mas com load_in_8bit=True
bnb_config = BitsAndBytesConfig(
    load_in_8bit=True,
)

# Resto igual...
```

### Opção 3: Full Fine-tuning (requer GPU com muita VRAM)

```python
# Sem quantização (requer ~28GB VRAM para Mistral-7B)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float16,
    device_map="auto",
)

# Sem LoRA - treina o modelo inteiro
# Resto do código sem PEFT
```

## 📊 Monitoramento com Weights & Biases

```python
# No início do script
import wandb

wandb.login()
wandb.init(project="music-identification-llm")

# Em training_args
training_args = TrainingArguments(
    # ... outros args
    report_to="wandb",
    run_name="mistral-7b-music-qlora"
)
```

## 🧪 Testar Modelo Treinado

```python
#!/usr/bin/env python3
"""Testa o modelo fine-tuned."""

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from peft import PeftModel
import torch

MODEL_PATH = "./music-lora-model"
BASE_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"

# Carregar modelo base
tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
model = AutoModelForCausalLM.from_pretrained(
    BASE_MODEL,
    torch_dtype=torch.float16,
    device_map="auto",
)

# Carregar adaptadores LoRA
model = PeftModel.from_pretrained(model, MODEL_PATH)
model = model.merge_and_unload()

# Pipeline de geração
generator = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=512,
    temperature=0.7,
)

# Teste
prompt = """<s>[INST] Identifique a música baseado na descrição.

User query: música sobre liberdade com um longo solo de guitarra, rock clássico dos anos 70 [/INST]"""

result = generator(prompt)
print(result[0]['generated_text'])
```

## 💡 Dicas de Otimização

### 1. Hiperparâmetros Recomendados

| Parâmetro | Valor Recomendado | Descrição |
|-----------|-------------------|-----------|
| `r` (rank) | 8-32 | Maior = mais capacidade, mais memória |
| `lora_alpha` | 2x rank | Geralmente 2× o valor de r |
| `learning_rate` | 1e-4 a 3e-4 | Menor para modelos maiores |
| `batch_size` | 4-16 | Ajustar conforme VRAM disponível |
| `epochs` | 3-5 | Mais pode causar overfitting |

### 2. Target Modules por Modelo

**Mistral/LLaMA:**
```python
target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
```

**GPT-2/GPT-Neo:**
```python
target_modules=["c_attn", "c_proj", "c_fc"]
```

**Phi-3:**
```python
target_modules=["qkv_proj", "o_proj", "gate_up_proj", "down_proj"]
```

### 3. Redução de Memória

```python
# Gradient checkpointing
training_args = TrainingArguments(
    gradient_checkpointing=True,
    gradient_accumulation_steps=8,  # Aumentar para simular batch maior
)

# Flash Attention 2 (muito mais rápido)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    attn_implementation="flash_attention_2",  # Requer instalação
)
```

## 📏 Avaliação do Modelo

```python
#!/usr/bin/env python3
"""Avalia o modelo em um conjunto de teste."""

from datasets import load_dataset
from sklearn.metrics import accuracy_score
import json

# Carregar test set
test_dataset = load_dataset("json", data_files="music_test.jsonl", split="train")

correct = 0
total = 0

for sample in test_dataset:
    prompt = format_instruction(sample)
    result = generator(prompt, max_new_tokens=256)[0]['generated_text']
    
    # Extrair resposta JSON
    try:
        predicted = json.loads(result.split("[/INST]")[1].strip())
        expected = json.loads(sample['output'])
        
        # Comparar song title e artist
        if (predicted['song'].lower() == expected['song'].lower() and 
            predicted['artist'].lower() == expected['artist'].lower()):
            correct += 1
        total += 1
    except:
        total += 1
        continue

accuracy = correct / total if total > 0 else 0
print(f"📊 Accuracy: {accuracy:.2%} ({correct}/{total})")
```

## 🔄 Deploy do Modelo Treinado

Após o treinamento, copie os arquivos para a pasta `models/`:

```bash
# Copiar modelo base + LoRA mesclado
cp -r ./music-lora-model/* ../llm-music-api/models/mistral-7b-music-lora/

# Ou manter apenas adaptadores LoRA separados
cp -r ./lora-weights/* ../llm-music-api/models/lora-weights/
```

Atualizar `.env`:
```bash
MODEL_PATH=/app/models/mistral-7b-music-lora
USE_LORA=false  # Se mesclou os pesos
# OU
USE_LORA=true
LORA_WEIGHTS_PATH=/app/models/lora-weights
```

## 📚 Recursos Adicionais

- [PEFT Documentation](https://huggingface.co/docs/peft)
- [QLoRA Paper](https://arxiv.org/abs/2305.14314)
- [TRL Library](https://huggingface.co/docs/trl)
- [Mistral Fine-tuning Guide](https://docs.mistral.ai/guides/finetuning/)

---

**Boa sorte com o fine-tuning! 🚀**
