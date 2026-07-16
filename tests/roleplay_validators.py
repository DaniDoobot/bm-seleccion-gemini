"""Classification and validation helper functions for candidate evaluation roleplay.

Extracted to a separate test module to keep test files clean.
"""

def check_role_swap(text: str) -> bool:
    """Verify if Gemini incorrectly swaps roles to act as clinic agent or evaluator."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    agent_phrases = [
        "voy a revisar su caso", "voy a revisar tu caso",
        "voy a consultar su ficha", "voy a consultar tu ficha",
        "voy a revisar su ficha", "voy a revisar tu ficha",
        "le voy a pasar", "te voy a pasar",
        "le pasare", "te pasare",
        "le transferire", "te transferire",
        "le pongo con el doctor", "te pongo con el doctor", "le pongo con el", "te pongo con el",
        "voy a transferir", "voy a pasarte", "voy a pasarle",
        "desde atencion al paciente", "desde la atencion al paciente",
        "en que puedo ayudar", "en que le puedo ayudar", "en que te puedo ayudar",
        "facilite sus datos", "facilite tus datos"
    ]
    if any(phrase in text_clean for phrase in agent_phrases):
        return True
        
    if "usted es el paciente" in text_clean or "tu eres el paciente" in text_clean or "como usted es el paciente" in text_clean:
        return True
        
    return False


def check_urgency(text: str) -> bool:
    """Verify if the patient spontaneously claims urgency (which is forbidden)."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    urgency_triggers = [
        "es urgente", "es una urgencia", "tengo una urgencia",
        "urgente que me atienda", "atencion medica urgente",
        "es algo grave", "pasando algo grave", "es grave"
    ]
    for trigger in urgency_triggers:
        if trigger in text_clean:
            idx = text_clean.find(trigger)
            prefix = text_clean[max(0, idx-15):idx]
            if any(neg in prefix for neg in ["no ", "no es ", "tampoco ", "nunca ", "sin "]):
                continue
            return True
    return False


def check_closure(text: str) -> bool:
    """Verify if the response attempts to close the test or the conversation."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    closure_phrases = [
        "la prueba ha terminado",
        "gracias por participar",
        "no puedo proporcionar feedback",
        "no puedo dar feedback",
        "hemos terminado",
        "finalizamos la llamada",
        "adios",
        "hasta luego"
    ]
    return any(p in text_clean for p in closure_phrases)


def check_full_disclosure(text: str) -> bool:
    """Verify if the response reveals the specific medical doubt (weekly relations frequency limit)."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    has_relations = any(w in text_clean for w in ["relacion", "relaciones", "sexo", "sexual", "sexuales", "coito", "intimo", "acto sexual"])
    has_frequency = any(w in text_clean for w in ["limite", "frecuencia", "cuantas", "cuantos", "numero", "cantidad", "cuanto", "veces"])
    has_weekly = any(w in text_clean for w in ["semana", "semanal", "semanales"])
    
    if has_relations and has_frequency and has_weekly:
        return True
        
    explicit_patterns = [
        "relaciones a la semana",
        "relaciones por semana",
        "frecuencia de relaciones",
        "frecuencia semanal"
    ]
    if any(pat in text_clean for pat in explicit_patterns) and has_frequency:
        return True
        
    return False


def check_partial_opening(text: str) -> bool:
    """Verify if the patient shows partial opening but without fully disclosing details."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    partial_triggers = [
        "cuestion intima", "tema intimo", "algo intimo",
        "contarte por encima", "explicar por encima", "contar por encima",
        "no me resulta facil", "no me siento comodo", "no me resulta comodo",
        "no es facil hablar de esto", "cuestion personal"
    ]
    if any(trigger in text_clean for trigger in partial_triggers):
        return True
        
    has_conjunction = any(c in text_clean for c in ["pero", "bueno", "en fin", "te puedo", "te lo", "solo que", "solo por encima", "solo un"])
    has_keywords = any(w in text_clean for w in ["personal", "intimo", "relacion", "relaciones", "sexual"])
    
    refusal_indicators = [
        "no pienso contartelo", "no te lo voy a decir", "no tengo por que contartelo",
        "pasame con el doctor y punto", "punto y final", "no quiero darte detalles",
        "no me apetece hablar", "solo quiero hablar con mi doctor", "solo al doctor",
        "no tengo por que contarle", "no tengo por que explicarle", "no me apetece contarlo",
        "no quiero contarselo a cualquiera", "no me apetece contarselo"
    ]
    is_closed_refusal = any(ref in text_clean for ref in refusal_indicators)
    
    if has_keywords and has_conjunction and not is_closed_refusal:
        return True
        
    return False


def check_resistance(text: str) -> bool:
    """Verify if the response expresses resistance to reveal details."""
    text_clean = text.lower()
    for a, b in [("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")]:
        text_clean = text_clean.replace(a, b)
    
    resistance_markers = [
        "hablar con el doctor", "hablar con el medico", "con el doctor", "con el medico",
        "no tengo por que", "no se lo voy a contar", "no quiero contarlo", "no quiero contarle",
        "no se lo tengo que explicar", "no tengo por que explicar", "no tengo por que dar",
        "prefiero hablarlo con el", "prefiero decirselo a el", "solo al doctor", "solo con el doctor",
        "hablar con el", "contar al doctor", "decir al doctor", "explicar al doctor", "tengo que hablar con el",
        "devolver la llamada", "pasar con el", "ponme con el", "trabas", "pegas", "perder el tiempo",
        "no tengo por que contartelo"
    ]
    if any(marker in text_clean for marker in resistance_markers):
        return True
        
    has_no_explain = ("no" in text_clean) and any(w in text_clean for w in ["contar", "explicar", "decir", "hablar", "dar", "revelar"])
    if has_no_explain:
        return True
        
    if "pasame" in text_clean or "transfiereme" in text_clean or "pásame" in text_clean:
        return True
        
    return False


def classify_response(text: str) -> str:
    """Classify the patient's response into one of the designated categories in priority order."""
    if check_role_swap(text):
        return "INVALID_ROLE_SWAP"
    if check_urgency(text):
        return "INVALID_URGENCY"
    if check_closure(text):
        return "INVALID_CLOSURE"
    if check_full_disclosure(text):
        return "FULL_DISCLOSURE"
    if check_partial_opening(text):
        return "PARTIAL_OPENING"
    if check_resistance(text):
        return "RESISTANCE"
    return "UNKNOWN"
