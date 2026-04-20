# 🎤 Fase 2 - Integração STT com worker_headless.py

## ✅ COMPLETO

**Status:** Pronto para uso  
**Data:** 20 de abril de 2026  
**Testes:** 4/4 ✅ passando

---

## 📝 O QUE FOI FEITO

### Código Modificado
- **src/worker_headless.py** (861 → 950+ linhas)
  - Importação condicional de STT módulos
  - `_init_stt()` - inicializa SpeechRecognizer e LyricsMatcher (lazy loading)
  - `_update_stt_lyrics()` - atualiza letras quando nova música é encontrada
  - `_start_stt_loop()` / `_stop_stt_loop()` - controla thread STT
  - `_stt_loop()` - loop principal que processa áudio e reconhece voz
  - Integração com callbacks existentes (3 novos: stt_recognized, stt_matched, sync_corrected)
  - Atualização automática do stop() para parar thread STT

### Novos Testes
- **scripts/test_stt_integration.py** (200 linhas)
  - Test 1: Importações STT ✅
  - Test 2: Inicialização do worker ✅
  - Test 3: Métodos STT funcionam ✅
  - Test 4: Callbacks STT funcionam ✅

---

## 🔌 INTEGRAÇÃO TÉCNICA

### Fluxo de Integração
```
1. Worker inicializa com config.ini
   ├─ Lê [SpeechSync] section
   ├─ Inicializa SpeechRecognizer (lazy loading)
   └─ Inicializa LyricsMatcher

2. Quando música é encontrada
   ├─ Fetch lyrics como antes
   ├─ Chama _update_stt_lyrics(lyrics_text)
   └─ Chama _start_stt_loop()

3. Thread STT roda paralelo
   ├─ Captura chunks de áudio (2.5s)
   ├─ Reconhece voz com faster-whisper
   ├─ Encontra match na letra com fuzzy matching
   ├─ Emite callbacks para atualizar UI
   └─ Pode atualizar linha em modo stt_only/hybrid

4. Quando worker para
   └─ Chama _stop_stt_loop() para parar thread STT
```

### Callbacks Disponíveis
```python
# Registrar handler
worker.on('stt_recognized', lambda text, confidence: ...)
worker.on('stt_matched', lambda line_index, similarity: ...)
worker.on('sync_corrected', lambda old_index, new_index: ...)

# Ou usar callbacks existentes
worker.on('status_changed', lambda msg: ...)  # Mensagens de status
```

---

## ⚙️ CONFIGURAÇÃO (config.ini)

```ini
[SpeechSync]
enabled = false                  # Kill switch (desabilitado por padrão)
mode = timestamp_only            # timestamp_only | stt_only | hybrid
model_size = tiny                # tiny | base | small
device = cuda                    # cuda (GPU) | cpu
chunk_duration_s = 2.5           # Duração de cada chunk
validation_interval_ms = 5000    # Intervalo de validação
min_similarity = 0.65            # Threshold de similaridade
auto_correct_threshold = 2       # Divergência para auto-correção
```

**Padrão atual:** Mode `timestamp_only` com `enabled = false`
- STT roda mas não afeta sync
- Seguro para validação

---

## 🧪 TESTAR INTEGRAÇÃO

```bash
# Rodar teste de integração
python scripts/test_stt_integration.py

# Resultado esperado
✅ Importações
✅ Inicialização
✅ Métodos STT
✅ Callbacks

Resultado: 4/4 testes passaram
```

---

## 🎯 ARQUITETURA

```
RecognitionWorkerHeadless (main thread)
├─ _recognition_loop()
│  ├─ Audio capture → API recognition
│  ├─ Fetch lyrics quando muda
│  ├─ _update_stt_lyrics() → config STT
│  └─ _start_stt_loop() → spawn STT thread
│
├─ _stt_loop() [NOVA THREAD]
│  ├─ capture_chunk(2.5s)
│  ├─ SpeechRecognizer.recognize_chunk()
│  ├─ emit('stt_recognized', text, confidence)
│  ├─ LyricsMatcher.find_best_match()
│  ├─ emit('stt_matched', line_index, similarity)
│  └─ [modo stt_only/hybrid] update _current_lyrics_index
│
└─ Callbacks registrados
   ├─ stt_recognized(text, confidence)
   ├─ stt_matched(line_index, similarity)
   └─ sync_corrected(old_index, new_index)
```

---

## 🚀 PRÓXIMOS PASSOS (Fase 3+)

### Fase 3: STT-Only Mode (2-3 dias)
- Ativar modo `stt_only` em config.ini
- Timecode sintético baseado em índice
- Validação com testes de áudio real

### Fase 4: Hybrid Mode (4-5 dias)
- Ativar modo `hybrid`
- Auto-correção com detecção de divergência
- Balancear timestamp vs STT

### Fase 5: Otimizações (3-4 dias)
- Compressão de modelo
- Cache de embeddings
- Paralelização de chunks

### Fase 6: QA & Release (2 dias)
- Testes completos com áudio real
- Documentação de usuário
- Release 1.0

---

## 📊 RESULTADOS

| Métrica | Resultado |
|---------|-----------|
| **Código** | 950+ linhas (worker_headless.py) |
| **Integração** | Callbacks + threads + config |
| **Testes** | 4/4 ✅ passando |
| **Compilação** | ✅ OK sem erros |
| **Thread Safety** | ✅ locks em dados compartilhados |
| **Kill Switch** | ✅ enabled = false por padrão |
| **Backward Compat** | ✅ 100% compatível |

---

## 💡 NOTAS

1. **STT é lazy-loaded**: Modelo só baixa na primeira música (não no startup)
2. **Sem blocking**: Thread separada evita afetar audio capture
3. **Seguro por padrão**: `enabled = false` mantém sistema estável
4. **3 modos**: timestamp_only (seguro), stt_only (arriscado), hybrid (recomendado)
5. **Callbacks extensíveis**: UI pode reagir a eventos STT em tempo real

---

**Status:** ✅ Fase 2 Completa  
**Próximo:** Fase 3 (STT-Only Mode)
