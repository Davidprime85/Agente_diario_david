"""
Core utilities: currency conversion, ID normalization, etc.
"""
import re
from typing import Union


def to_float(value: Union[str, float, int]) -> float:
    """
    Converte string de moeda (BR ou EN) para float.
    Aceita "50,00", "50.00", " 50 , 20 " ou número direto.
    
    Args:
        value: String ou número a ser convertido
        
    Returns:
        float: Valor convertido
        
    Raises:
        ValueError: Se não conseguir converter
    """
    if isinstance(value, (int, float)):
        return float(value)
    
    if not isinstance(value, str):
        raise ValueError(f"Tipo inválido para conversão: {type(value)}")
    
    # Remove espaços e caracteres especiais (exceto vírgula e ponto)
    cleaned = re.sub(r'[^\d,.]', '', value.strip())
    
    # Se tem vírgula, assume formato BR (50,00)
    if ',' in cleaned:
        # Remove pontos (milhares) e substitui vírgula por ponto
        cleaned = cleaned.replace('.', '').replace(',', '.')
    
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError(f"Não foi possível converter '{value}' para número")


def format_currency_br(value: float) -> str:
    """Formata float para string no formato brasileiro (50,00)"""
    return f"{value:.2f}".replace('.', ',')


def ensure_string_id(chat_id: Union[str, int]) -> str:
    """
    REGRA 1: Garante que chat_id seja sempre string.
    Usado em TODAS as interações com Firestore.
    """
    return str(chat_id)
