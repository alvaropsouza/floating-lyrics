# Plano de Implementação - Speech-to-Text Sync

## 🎯 Resumo Executivo

Implementar sincronização inteligente de letras usando reconhecimento de voz (STT) para:
- ✅ Auto-correção de drift/dessincronização
- ✅ Suporte a letras sem timestamps LRC
- ✅ Detecção de seeks/pulos de música
- ✅ Sincronização mais precisa e robusta

**Biblioteca escolhida:** faster-whisper (modelo tiny/base)  
**Latência esperada:** 400-700ms  
**Precisão com música:** 75-85%  
**Requisitos:** GPU recomendada (RTX 3060+)

---

## 📋 Checklist de Implementação

### ✅ Fase 0: Planejamento (COMPLETO)
- [x] Pesquisa de bibliotecas STT
- [x] Definição de arquitetura
- [x] Especificação técnica completa
- [x] Identificação de pontos de integração

### 🔄 Fase 1: Setup e Infraestrutura (PRÓXIMA)

**Objetivo:** Criar módulos base sem integração ainda

**Tarefas:**

1. **Instalar dependências**
   ```bash
   pip install faster-whisper==1.1.0 ctranslate2==4.5.0
   ```

2. **Criar `src/speech_recognition.py`**
   - [ ] Classe `SpeechRecognizer`
   - [ ] Método `recognize_chunk()` básico
   - [ ] Lazy loading do modelo Whisper
   - [ ] Configuração tiny/base/small
   - [ ] Device auto-detection (cuda/cpu)

3. **Criar `src/lyrics_matcher.py`**
   - [ ] Classe `LyricsMatcher`
   - [ ] Normalização de texto
   - [ ] Fuzzy matching com SequenceMatcher
   - [ ] Context-aware search (busca próxima à posição atual)
   - [ ] Threshold de similaridade configurável

4. **Testes unitários**
   ```bash
   # Criar tests/test_speech_recognition.py
   # Criar tests/test_lyrics_matcher.py
   ```
   - [ ] Teste de reconhecimento com áudio de sample
   - [ ] Teste de matching com letras conhecidas
   - [ ] Teste de edge cases (texto vazio, sem match, etc.)

**Tempo estimado:** 2-3 dias

---

### 🔄 Fase 2: Integração com Worker

**Objetivo:** Integrar STT no pipeline existente sem alterar comportamento padrão

**Tarefas:**

1. **Adicionar configuração em `config.ini`**
   ```ini
   [SpeechSync]
   enabled = false  # Começa desabilitado
   mode = hybrid
   model_size = tiny
   device = cuda
   chunk_duration_s = 2.5
   validation_interval_ms = 5000
   min_similarity = 0.65
   auto_correct_threshold = 2
   ```

2. **Modificar `src/worker_headless.py`**
   - [ ] Import dos novos módulos
   - [ ] Adicionar flags e configuração STT no `__init__`
   - [ ] Método `_init_stt()` com lazy loading
   - [ ] Criar `_stt_loop()` thread básica
   - [ ] Adicionar novos callbacks: `stt_recognized`, `stt_matched`, `sync_corrected`
   - [ ] Integrar `_lyrics_matcher.set_lyrics()` em `_on_lyrics_fetched()`
   - [ ] Iniciar/parar STT thread no `run()` e `stop()`

3. **Modo Baseline (timestamp_only)**
   - [ ] STT roda mas NÃO altera sincronização
   - [ ] Apenas loga resultados para validação
   - [ ] Permite testar STT sem quebrar funcionamento atual

4. **Testes de integração**
   - [ ] Verificar que threads não bloqueiam
   - [ ] Callbacks emitidos corretamente
   - [ ] Worker para/inicia sem erros
   - [ ] Modo desabilitado = zero overhead

**Tempo estimado:** 3-4 dias

---

### 🔄 Fase 3: Modo STT Puro

**Objetivo:** Sincronizar músicas SEM timestamps LRC

**Tarefas:**

1. **Implementar `_apply_stt_only_sync()` no worker**
   - [ ] Criar timecode sintético baseado em índice da linha
   - [ ] Emitir `timecode_updated` com timecode sintético
   - [ ] Atualizar `_last_timecode_ms` e `_last_timecode_update`

2. **Ajustar `LyricsMatcher` para músicas longas**
   - [ ] Otimizar busca para letras com 100+ linhas
   - [ ] Sliding window search
   - [ ] Cache de matches recentes

3. **Testes com músicas reais**
   - [ ] Testar com 5-10 músicas sem LRC
   - [ ] Verificar precisão de sincronização
   - [ ] Medir latência real

**Tempo estimado:** 2-3 dias

---

### 🔄 Fase 4: Modo Híbrido (Principal)

**Objetivo:** Combinar timestamp + STT para sincronização robusta

**Tarefas:**

1. **Implementar detecção de divergência**
   - [ ] Método `_process_stt_match()` no worker
   - [ ] Calcular divergência entre STT e timestamp
   - [ ] Threshold configurável (padrão: 2 linhas)

2. **Implementar auto-correção**
   - [ ] Método `_apply_hybrid_sync_correction()`
   - [ ] Recalcular timecode baseado em linha do STT
   - [ ] Integrar com `lyrics_parser.py` para obter timestamps LRC
   - [ ] Emitir evento `sync_corrected`

3. **Integração com LRC parser**
   - [ ] Método `_get_timecode_for_line(line_index)` no worker
   - [ ] Parse de LRC em memória (já parseado em `_on_lyrics_fetched`)
   - [ ] Mapear índice → timestamp

4. **Testes de correção**
   - [ ] Simular seeks manuais
   - [ ] Verificar que correção acontece em 2-5s
   - [ ] Evitar correções "ping-pong" (oscilar entre posições)

**Tempo estimado:** 4-5 dias

---

### 🔄 Fase 5: Otimizações

**Objetivo:** Performance, UX e polish

**Tarefas:**

1. **Otimizações de performance**
   - [ ] Resampling eficiente 44.1kHz → 16kHz (evitar numpy simples)
   - [ ] Lazy model loading (carregar só quando enabled=true)
   - [ ] Limitar uso de memória (buffer de chunks limitado)
   - [ ] VAD para não processar partes instrumentais

2. **Tuning de parâmetros**
   - [ ] Testar diferentes chunk_duration (1.5s, 2.5s, 3.5s)
   - [ ] Ajustar min_similarity (testar 0.55, 0.65, 0.75)
   - [ ] Ajustar auto_correct_threshold (1, 2, 3 linhas)
   - [ ] Benchmark com dataset de 20+ músicas

3. **UX/Feedback visual**
   - [ ] Adicionar indicador "🎤 STT Ativo" na UI
   - [ ] Mostrar confiança do STT (opcional)
   - [ ] Notificar correções ("Sincronização ajustada")
   - [ ] Logs estruturados para debug

**Tempo estimado:** 3-4 dias

---

### 🔄 Fase 6: Testes Finais e Docs

**Objetivo:** Validação completa e documentação

**Tarefas:**

1. **Testes end-to-end**
   - [ ] Dataset de 30+ músicas variadas (pop, rock, rap, sertanejo, etc.)
   - [ ] Com/sem LRC
   - [ ] Diferentes qualidades de áudio
   - [ ] Músicas com features/colaborações

2. **Benchmark de performance**
   - [ ] Medir latência média/p95/p99
   - [ ] Medir precisão de matching
   - [ ] Medir uso de CPU/GPU/RAM
   - [ ] Comparar com baseline (timestamp only)

3. **Documentação**
   - [ ] Atualizar [README.md](../README.md) com feature STT
   - [ ] Guia de configuração
   - [ ] Troubleshooting (erros comuns)
   - [ ] FAQ (GPU necessária? Funciona offline? etc.)

**Tempo estimado:** 2 dias

---

## 📊 Timeline Total

| Fase | Duração | Dependências | Status |
|------|---------|--------------|--------|
| 0. Planejamento | 1 dia | - | ✅ COMPLETO |
| 1. Setup | 2-3 dias | Fase 0 | ⏳ PRÓXIMA |
| 2. Integração | 3-4 dias | Fase 1 | ⏸️ Aguardando |
| 3. STT Puro | 2-3 dias | Fase 2 | ⏸️ Aguardando |
| 4. Híbrido | 4-5 dias | Fase 3 | ⏸️ Aguardando |
| 5. Otimizações | 3-4 dias | Fase 4 | ⏸️ Aguardando |
| 6. Testes Finais | 2 dias | Fase 5 | ⏸️ Aguardando |

**Total: 17-22 dias** (~3-4 semanas)

---

## 🎯 Critérios de Sucesso

### Requisitos Mínimos (MVP)

- ✅ STT reconhece voz cantada com >70% de precisão
- ✅ Modo hybrid corrige drift em músicas com LRC
- ✅ Modo stt_only sincroniza músicas sem LRC
- ✅ Latência < 1 segundo
- ✅ Zero crashes ou memory leaks
- ✅ Pode ser desabilitado (fallback para timestamp only)

### Requisitos Desejáveis

- ⭐ Precisão >80% com música pop/rock
- ⭐ Latência <700ms (modelo tiny em GPU RTX 3060+)
- ⭐ Auto-correção de seeks em <3 segundos
- ⭐ Uso de GPU <1GB VRAM
- ⭐ Funciona em CPU (com latência maior)

### Requisitos Opcionais

- 🎁 VAD automático (economia de recursos)
- 🎁 Detecção de idioma (multi-language)
- 🎁 Fine-tuned model (maior precisão com música)
- 🎁 Fallback Vosk para hardware fraco

---

## ⚙️ Comandos Úteis

### Instalar dependências
```bash
pip install faster-whisper==1.1.0 ctranslate2==4.5.0
```

### Baixar modelo (manual, opcional)
```bash
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny')"
```

### Rodar testes
```bash
pytest tests/test_speech_recognition.py -v
pytest tests/test_lyrics_matcher.py -v
```

### Benchmark manual
```bash
python scripts/test_recognition.py --enable-stt --model tiny
```

### Habilitar STT
```ini
# Editar config.ini
[SpeechSync]
enabled = true
```

---

## 📞 Suporte e Referências

- **Documentação completa:** [docs/SPEECH_TO_TEXT_SYNC.md](./SPEECH_TO_TEXT_SYNC.md)
- **Arquitetura geral:** [docs/ARCHITECTURE.md](./ARCHITECTURE.md)
- **faster-whisper docs:** https://github.com/SYSTRAN/faster-whisper

---

**Próximo passo:** Começar Fase 1 - Setup e Infraestrutura 🚀
