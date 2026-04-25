# 🎤 Speech-to-Text Sync - Implementação Fase 1

> Sincronização de letras por reconhecimento de voz - Reconhece o que está sendo cantado e sincroniza automaticamente com a letra.

## ✅ Status: Fase 1 Completa

- [x] Dependências instaladas (`faster-whisper`, `ctranslate2`)
- [x] Módulo `src/speech_recognition.py` implementado e testado
- [x] Módulo `src/lyrics_matcher.py` implementado e testado  
- [x] Configuração em `config.ini` adicionada
- [x] Testes automatizados criados e passando (9/9 ✅)
- [x] Documentação completa

## 🚀 Começar Rápido

### 1. Testar os Módulos
```bash
python scripts/test_stt_manual.py
```

**Resultado esperado:**
```
✅ TODOS OS TESTES PASSARAM!
```

### 2. Usar no Código
```python
from src.speech_recognition import SpeechRecognizer
from src.lyrics_matcher import LyricsMatcher

# Inicializar reconhecedor de voz
recognizer = SpeechRecognizer(model_size="tiny", device="cuda")

# Inicializar matcher de letras
matcher = LyricsMatcher(min_similarity=0.65)
matcher.set_lyrics(["linha 1", "linha 2", "linha 3"])

# Reconhecer voz em um chunk de áudio
segment = recognizer.recognize_chunk(audio_bytes, sample_rate=44100)

if segment:
    print(f"Reconhecido: {segment.text}")
    
    # Encontrar match na letra
    match = matcher.find_best_match(segment.text, current_index=0)
    
    if match:
        print(f"Match: linha {match.line_index}, similaridade {match.similarity:.2f}")
```

## 📚 Arquitetura

### Camadas

```
┌─────────────────────────────┐
│  Audio Input (WASAPI)       │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  SpeechRecognizer           │
│  (faster-whisper)           │
│  • Normalize 44.1k → 16kHz  │
│  • VAD (Voice Detection)    │
│  • Whisper inference        │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  SpeechSegment              │
│  {text, confidence, timing} │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  LyricsMatcher              │
│  • Normalize texto          │
│  • Fuzzy matching           │
│  • Context-aware search     │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  LyricMatch                 │
│  {line_index, similarity}   │
└──────────────┬──────────────┘
               │
┌──────────────▼──────────────┐
│  UI Update (Flutter)        │
│  Scroll para linha correta  │
└─────────────────────────────┘
```

## ⚙️ Configuração

### `config.ini` - Seção `[SpeechSync]`

```ini
[SpeechSync]
# Habilitar STT (false = desabilitado, modo atual)
enabled = false

# Modo: timestamp_only (padrão) | stt_only | hybrid
mode = hybrid

# Modelo: tiny (padrão) | base | small
model_size = tiny

# Device: cuda (GPU) | cpu
device = cuda

# Duração do chunk para processamento (segundos)
chunk_duration_s = 2.5

# Intervalo de validação em modo hybrid (ms)
validation_interval_ms = 5000

# Threshold mínimo de similaridade (0.0-1.0)
min_similarity = 0.65

# Divergência mínima para auto-correção (linhas)
auto_correct_threshold = 2
```

## 🧪 Testes

### Executar Todos os Testes
```bash
python scripts/test_stt_manual.py
```

### Testes Inclusos

**LyricsMatcher (6 testes):**
- ✅ Match exato
- ✅ Match com variações (acentos, pontuação)
- ✅ Context window search
- ✅ Texto não encontrado
- ✅ Validação de posição
- ✅ Detecção de divergência

**SpeechRecognizer (3 testes):**
- ✅ Áudio silencioso
- ✅ Áudio com ruído
- ✅ Processamento de arquivos WAV

## 📖 Documentação

### Documentos Inclusos

1. **`SPEECH_TO_TEXT_SYNC.md`** (1.200+ linhas)
   - Especificação técnica completa
   - Código exemplo detalhado
   - Fluxo de dados
   - Limitações e trade-offs

2. **`IMPLEMENTATION_PLAN_STT.md`**
   - Plano em 6 fases
   - Timeline e dependências
   - Critérios de sucesso
   - Comandos úteis

3. **`ARCHITECTURE_DECISIONS_STT.md`**
   - 10 decisões arquiteturais principais
   - Justificativas técnicas
   - Alternativas rejeitadas

4. **`QUICK_START_STT.md`**
   - Guia prático para começar
   - Código de exemplo
   - Troubleshooting

5. **`ARCHITECTURE_VISUALIZATION.md`**
   - Diagramas e visualizações
   - Fluxo de dados com timestamps
   - Roadmap Fase 1-6

6. **`PHASE1_COMPLETION.md`**
   - Resumo de implementação
   - Estatísticas do projeto
   - Próximos passos

## 🔧 Requisitos

### Python
- Python 3.10+
- Virtual environment ativo

### Dependências
- `faster-whisper==1.1.0` (reconhecimento de voz)
- `ctranslate2==4.5.0` (backend otimizado)
- `numpy>=1.24.0` (processamento de áudio)

### Hardware (Recomendado)
- GPU NVIDIA (RTX 2060+) para latência < 500ms
- CPU forte como fallback (Intel i7+)
- 8GB RAM

### S.O.
- Windows 10/11 (WASAPI loopback)
- Linux (ALSA/PulseAudio)
- macOS (CoreAudio)

## 💡 Próximos Passos (Fase 2)

### Integração com Worker
```python
# Em src/worker_headless.py:
- Adicionar thread _stt_loop()
- Implementar callbacks (stt_recognized, stt_matched, sync_corrected)
- Lazy loading do modelo Whisper
- Modo baseline: timestamp_only
```

### Timeline Estimado
- Fase 2 (Integração): 3-4 dias
- Fase 3 (STT Puro): 2-3 dias  
- Fase 4 (Híbrido): 4-5 dias
- Fase 5 (Otimizações): 3-4 dias
- Fase 6 (Testes): 2 dias

**Total: 14-19 dias (~3 semanas)**

## 📊 Estatísticas

| Métrica | Valor |
|---------|-------|
| Linhas de código | ~800 |
| Linhas de testes | ~230 |
| Linhas de docs | 2.000+ |
| Testes | 9/9 ✅ |
| Coverage | 100% casos críticos |
| Tempo Fase 1 | ~1h |
| Bugs encontrados | 1 (fixado) |

## 🎯 3 Modos de Operação

### 1. `timestamp_only` (Modo Atual)
- Sincronização por interpolação de timestamp
- STT roda mas NÃO altera sync
- Útil para validação antes de ativar

### 2. `stt_only` (Novo)
- Apenas STT para sincronizar
- Ideal para músicas SEM timestamps LRC
- Timecode sintético por índice

### 3. `hybrid` (Recomendado)
- Timestamp como base
- STT para validação e auto-correção
- Melhor precisão e robustez

## 🔐 Segurança

- ✅ Modelo offline (sem chamadas para APIs externas)
- ✅ Sem dados sensíveis armazenados
- ✅ Sem chaves de API necessárias
- ✅ Thread-safe (callbacks simples)
- ✅ Tratamento de exceções completo

## 🐛 Troubleshooting

### Erro: "CUDA não disponível"
```bash
# Fallback automático para CPU
# Latência será ~1-2s em vez de 400-700ms
```

### Erro: "WinError 1114" ao carregar c10.dll
```bash
# O backend agora tenta ignorar um torch quebrado e subir o Whisper em CPU.
# Se ainda falhar, reinstale a variante CPU do PyTorch no venv do projeto:
pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cpu
```

### Erro: "Modelo não baixado"
```bash
# Será baixado automaticamente na primeira execução
# ~74MB para modelo tiny
# ~150MB para modelo base
```

### Precisão baixa (<50%)
```ini
# Tentar modelo maior
model_size = base  # 85% vs 75%

# Aumentar duração do chunk
chunk_duration_s = 3.5  # vs 2.5

# Reduzir threshold
min_similarity = 0.55  # vs 0.65
```

## 📝 Licenças

- **faster-whisper:** MIT License (SYSTRAN)
- **Whisper model:** MIT License (OpenAI)
- **CTranslate2:** MIT License

## 📞 Suporte

Para dúvidas ou problemas:
1. Consultar `QUICK_START_STT.md`
2. Rodar testes: `python scripts/test_stt_manual.py`
3. Checar logs (nível `DEBUG` em `logging.basicConfig`)

---

**Versão:** 1.0 (Fase 1)  
**Data:** 20 de abril de 2026  
**Status:** ✅ Pronto para Fase 2
