"""
Core utilities: currency conversion, ID normalization, etc.
"""
import re
from typing import Any, Union


def to_float(value: Any) -> float:
    """
    Extrai o valor numérico de qualquer texto, ignorando R$, espaços e letras.
    Exemplos:
    "R$ 50,00" -> 50.0
    "50.00" -> 50.0
    "Adicione gasto 50,00 lanche" -> 50.0
    "Gastei 50 reais" -> 50.0
    """
    if value is None:
        return 0.0

    if isinstance(value, (float, int)):
        return float(value)

    text = str(value).strip()

    # REGEX: Busca apenas números, pontos e vírgulas (ignora R$, "Gasto", etc.)
    match = re.search(r'[\d.,]+', text)

    if not match:
        return 0.0

    clean_num = match.group(0)

    # Lógica Brasil vs EUA
    # Se tem vírgula, remove pontos de milhar e troca vírgula por ponto
    if "," in clean_num:
        clean_num = clean_num.replace(".", "")  # Tira milhar (1.000,00 -> 1000,00)
        clean_num = clean_num.replace(",", ".")  # Decimal (1000,00 -> 1000.00)

    try:
        return float(clean_num)
    except ValueError:
        return 0.0


def format_currency_br(value: float) -> str:
    """Formata float para string no formato brasileiro (50,00)"""
    return f"{value:.2f}".replace('.', ',')


def ensure_string_id(chat_id: Union[str, int]) -> str:
    """
    REGRA 1: Garante que chat_id seja sempre string.
    Usado em TODAS as interações com Firestore.
    """
    return str(chat_id)
