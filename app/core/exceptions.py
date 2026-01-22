"""
Custom exceptions
"""


class CurrencyConversionError(Exception):
    """Erro ao converter valor de moeda"""
    pass


class FirestoreError(Exception):
    """Erro nas operações do Firestore"""
    pass


class GoogleServiceError(Exception):
    """Erro nos serviços Google"""
    pass
