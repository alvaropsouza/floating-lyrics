# Binários e Executáveis

Esta pasta contém executáveis e binários necessários para o funcionamento do projeto.

## Arquivos

- **`fpcalc.exe`** - Chromaprint fingerprinting tool
  - Usado pelo AcoustID para gerar fingerprints de áudio
  - Versão Windows do [Chromaprint](https://acoustid.org/chromaprint)
  - Requerido apenas se você usar o provedor AcoustID para reconhecimento

## Download

Se `fpcalc.exe` não estiver presente, você pode baixá-lo em:
- https://acoustid.org/chromaprint
- Ou o projeto tentará usar a versão disponível no PATH do sistema
