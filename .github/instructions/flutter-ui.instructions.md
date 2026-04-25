---
applyTo: "flutter_ui/lib/**/*.dart"
description: "Diretrizes de manutenabilidade e consistencia visual do frontend Flutter"
---

Objetivo
- Manter o frontend pequeno por arquivo, previsivel e com layout consistente.

Estrutura
- Preferir widgets pequenos e reutilizaveis em vez de acumular blocos visuais longos em uma unica tela.
- Extrair constantes visuais (cores, raios, espacamentos) para arquivos de tema/tokens.
- Evitar misturar logica de parse de eventos, estado de rede e composicao visual no mesmo arquivo quando a extracao for simples.

Layout
- Evitar ajustes visuais fragis com margens magicas e `Transform.scale` quando um `SizedBox`, `Align` ou widget dedicado resolver melhor.
- Centralizar padding horizontal/vertical recorrente em tokens reutilizaveis.
- Para componentes com alinhamento sensivel, criar widget proprio em vez de repetir `Row`/`Container` inline.

Estado
- Em services, preferir metodos privados pequenos por tipo de evento em vez de um unico bloco `switch` muito grande.
- Ao resetar estado derivado (traducao, timecode, flags), encapsular em helpers para evitar divergencia entre eventos.

UX
- Manter textos em Portugues.
- Fallbacks visuais devem degradar para estados legiveis, nao para layout quebrado.