"""
Fuzzy matching de texto reconhecido (STT) com letras de música.

Usado para encontrar a posição atual na letra baseado no que foi reconhecido
pelo speech-to-text.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

_LOG = logging.getLogger(__name__)


@dataclass
class LyricMatch:
    """Resultado de matching entre texto reconhecido e letra."""
    line_index: int       # Índice da linha na letra (0-based)
    similarity: float     # Similaridade 0.0 a 1.0
    matched_text: str     # Texto da linha que deu match
    recognized_text: str  # Texto que foi reconhecido pelo STT


class LyricsMatcher:
    """
    Encontra a posição atual na letra baseado em texto reconhecido por STT.
    
    Usa fuzzy matching (difflib.SequenceMatcher) tolerante a:
    - Erros de reconhecimento de voz
    - Palavras parciais
    - Variações de pronúncia/escrita
    - Acentuação e pontuação
    """
    
    def __init__(self, min_similarity: float = 0.6):
        """
        Inicializa matcher.
        
        Args:
            min_similarity: Similaridade mínima para considerar match válido (0.0-1.0)
                           Valores típicos:
                           - 0.5: Muito permissivo (pode dar falsos positivos)
                           - 0.65: Balanceado (recomendado)
                           - 0.8: Muito restritivo (pode perder matches válidos)
        """
        self.min_similarity = min_similarity
        self._cached_lyrics: List[str] = []
        self._normalized_lyrics: List[str] = []
        self._match_cache: dict[str, Optional[LyricMatch]] = {}  # Cache de matches recentes
        
    def set_lyrics(self, lyrics: List[str]) -> None:
        """
        Define letra da música atual.
        
        Args:
            lyrics: Lista de linhas da letra
        """
        self._cached_lyrics = lyrics
        self._normalized_lyrics = [
            self._normalize_text(line) for line in lyrics
        ]
        self._match_cache.clear()  # Limpar cache ao trocar de música
        
        _LOG.info(f"🔍 Lyrics matcher configurado com {len(lyrics)} linhas")
        _LOG.debug(f"Primeiras 3 linhas (normalizadas): {self._normalized_lyrics[:3]}")
    
    @staticmethod
    def _normalize_text(text: str) -> str:
        """
        Normaliza texto para matching.
        
        Remove acentos, pontuação, converte para lowercase, normaliza espaços.
        
        Args:
            text: Texto original
            
        Returns:
            Texto normalizado
        """
        if not text:
            return ""
        
        # Remover acentos (NFD decomposition + ignorar combining characters)
        text = unicodedata.normalize('NFKD', text)
        text = text.encode('ascii', 'ignore').decode('ascii')
        
        # Lowercase
        text = text.lower()
        
        # Remover pontuação e caracteres especiais (manter apenas letras e espaços)
        text = re.sub(r'[^\w\s]', '', text)
        
        # Normalizar espaços múltiplos
        text = ' '.join(text.split())
        
        return text
    
    def find_best_match(
        self, 
        recognized_text: str,
        context_window: int = 10,
        current_index: int = 0
    ) -> Optional[LyricMatch]:
        """
        Encontra melhor match na letra para o texto reconhecido.
        
        Estratégia de busca em 2 fases:
        1. Buscar em janela próxima da posição atual (otimização)
        2. Se não achar bom match, buscar no resto da letra
        
        Args:
            recognized_text: Texto reconhecido pelo STT
            context_window: Quantas linhas antes/depois da posição atual considerar
            current_index: Índice atual estimado (para otimização)
            
        Returns:
            LyricMatch com melhor correspondência ou None se não encontrou
        """
        if not self._normalized_lyrics or not recognized_text:
            return None
        
        # Verificar cache
        cache_key = f"{recognized_text}|{current_index}"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]
        
        normalized_input = self._normalize_text(recognized_text)
        
        if not normalized_input:
            return None
        
        best_match: Optional[LyricMatch] = None
        best_similarity = 0.0
        
        # Fase 1: Buscar em janela próxima da posição atual (mais provável)
        start_idx = max(0, current_index - context_window)
        end_idx = min(len(self._normalized_lyrics), current_index + context_window)
        
        for i in range(start_idx, end_idx):
            similarity = self._calculate_similarity(
                normalized_input, 
                self._normalized_lyrics[i]
            )
            
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = LyricMatch(
                    line_index=i,
                    similarity=similarity,
                    matched_text=self._cached_lyrics[i],
                    recognized_text=recognized_text
                )
        
        # Fase 2: Se não achou bom match, buscar no resto da letra
        if best_similarity < self.min_similarity:
            for i in range(len(self._normalized_lyrics)):
                # Pular linhas já verificadas na fase 1
                if start_idx <= i < end_idx:
                    continue
                    
                similarity = self._calculate_similarity(
                    normalized_input, 
                    self._normalized_lyrics[i]
                )
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = LyricMatch(
                        line_index=i,
                        similarity=similarity,
                        matched_text=self._cached_lyrics[i],
                        recognized_text=recognized_text
                    )
        
        # Cachear resultado (limitar tamanho do cache)
        if len(self._match_cache) > 100:
            # Remover metade mais antiga (dict é ordered desde Python 3.7+)
            for _ in range(50):
                self._match_cache.pop(next(iter(self._match_cache)))
        
        # Retornar apenas se similaridade acima do threshold
        if best_match and best_match.similarity >= self.min_similarity:
            _LOG.debug(
                f"🔍 Match encontrado: linha {best_match.line_index} "
                f"(sim={best_match.similarity:.2f}) '{best_match.matched_text[:50]}'"
            )
            self._match_cache[cache_key] = best_match
            return best_match
        
        _LOG.debug(
            f"🔍 Nenhum match encontrado para '{recognized_text}' "
            f"(melhor: {best_similarity:.2f} < threshold {self.min_similarity})"
        )
        self._match_cache[cache_key] = None
        return None
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Calcula similaridade entre dois textos.
        
        Usa algoritmo Ratcliff-Obershelp (SequenceMatcher) que é bom para
        texto com erros e variações.
        
        Combina:
        - Similaridade global (sequência completa)
        - Overlap de palavras individuais
        
        Args:
            text1: Primeiro texto (normalizado)
            text2: Segundo texto (normalizado)
            
        Returns:
            Similaridade de 0.0 (nada em comum) a 1.0 (idênticos)
        """
        if not text1 or not text2:
            return 0.0
        
        # Similaridade de sequência global
        global_sim = SequenceMatcher(None, text1, text2).ratio()
        
        # Bonus se palavras-chave coincidem
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if words1 and words2:
            # Jaccard similarity de palavras
            word_overlap = len(words1 & words2) / len(words1 | words2)
            
            # Média ponderada: 70% sequência global, 30% overlap de palavras
            # Isso ajuda quando STT reconhece palavras corretas mas em ordem diferente
            return global_sim * 0.7 + word_overlap * 0.3
        
        return global_sim
    
    def validate_position(
        self, 
        recognized_text: str, 
        expected_index: int,
        tolerance: int = 2
    ) -> bool:
        """
        Valida se posição atual está correta baseado em texto reconhecido.
        
        Usado no modo híbrido para verificar se sincronização por timestamp
        está correta ou precisa de correção.
        
        Args:
            recognized_text: Texto reconhecido pelo STT
            expected_index: Índice esperado da linha atual
            tolerance: Quantas linhas de margem permitir (padrão: 2)
            
        Returns:
            True se validado (dentro da tolerância), False se divergiu
        """
        match = self.find_best_match(
            recognized_text, 
            context_window=tolerance,
            current_index=expected_index
        )
        
        if not match:
            _LOG.debug(f"Validação: sem match para '{recognized_text}'")
            return False
        
        divergence = abs(match.line_index - expected_index)
        is_valid = divergence <= tolerance
        
        if not is_valid:
            _LOG.warning(
                f"⚠️ Validação falhou: esperado linha {expected_index}, "
                f"STT indicou linha {match.line_index} (divergência={divergence})"
            )
        else:
            _LOG.debug(f"✅ Validação OK: linha {match.line_index} (divergência={divergence})")
        
        return is_valid
    
    def get_context_lines(self, index: int, before: int = 2, after: int = 2) -> List[str]:
        """
        Retorna linhas de contexto ao redor de um índice.
        
        Útil para debugging e logging.
        
        Args:
            index: Índice central
            before: Quantas linhas antes incluir
            after: Quantas linhas depois incluir
            
        Returns:
            Lista de linhas de contexto
        """
        start = max(0, index - before)
        end = min(len(self._cached_lyrics), index + after + 1)
        return self._cached_lyrics[start:end]
