---
applyTo: "**/*.py"
description: "Diretrizes do projeto Floating Lyrics para mudanças em captura de audio, reconhecimento e sincronizacao de letras"
---

Objetivo
- Priorizar estabilidade da sincronizacao da letra e responsividade da UI.

Regras tecnicas
- Nao bloquear a thread da interface Qt. Processos de rede e captura devem permanecer no worker.
- Em sincronizacao de tempo, usar relogio monotonic com time.perf_counter para calcular deltas.
- Evitar chamadas de rede repetidas para a mesma musica; usar cache em memoria quando possivel.
- Preservar compatibilidade com Windows 10/11 e captura WASAPI loopback.

Padrao de mudancas
- Alterar o minimo necessario e manter estilo ja existente nos arquivos.
- Evitar renomear sinais, slots e chaves de configuracao sem necessidade.
- Se incluir nova configuracao, definir fallback seguro e persistencia no config.ini via config.py.

UX e linguagem
- Manter textos de status e mensagens em Portugues.
- Quando houver erro de rede/API, retornar mensagem curta e acionavel para o usuario.

Seguranca
- Nao inserir chaves de API reais no codigo, exemplos, docs ou commits.
