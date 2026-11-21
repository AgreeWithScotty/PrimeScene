"""Classify explicit storefront access evidence without equating entitlement with Prime."""
import re
from urllib.parse import urlsplit


def access_evidence(node):
    """Return allowlisted facts only, never arbitrary offer/account payloads."""
    cues = node.get('entitlementCues') or {}
    cues = cues if isinstance(cues, dict) else {}
    facts = set()
    for owner in (node, cues):
        if any(owner.get(key) is True for key in ('isPrime', 'isIncludedWithPrime', 'includedWithPrime')):
            facts.add('prime_flag')
        if str(owner.get('offerClassification') or '').upper() == 'PRIME':
            facts.add('prime_classification')
        for key in ('benefitId', 'benefitProgramId', 'subscriptionId'):
            if str(owner.get(key) or '').casefold() == 'prime':
                facts.add('prime_benefit')
        if any(owner.get(key) is True for key in ('isPurchased', 'isOwned', 'purchased')):
            facts.add('owned_flag')
        if any(owner.get(key) is True for key in ('isRented', 'rented')):
            facts.add('rental_flag')
    # Real storefront cards identify the benefit on the provider logo. A generic
    # Prime Video image or an ENTITLED_ICON is not evidence of Prime inclusion.
    logo = (cues.get('providerLogo') or {}).get('imageUrl', '') if isinstance(cues.get('providerLogo'), dict) else ''
    if isinstance(logo, str):
        try:
            parts = urlsplit(logo)
        except ValueError:
            parts = urlsplit('')
        if parts.hostname in ('m.media-amazon.com', 'images-na.ssl-images-amazon.com'):
            if re.search(r'/benefit-id/[^/]+/prime/', parts.path, re.I):
                facts.add('prime_provider')
            elif '/benefit-id/' in parts.path or '/channel-id/' in parts.path:
                facts.add('other_provider')
    for key in ('focusMessage', 'compactFocusMessage', 'buyboxMessage', 'glanceMessage'):
        value = cues.get(key)
        message = value.get('message') if isinstance(value, dict) else ''
        if not isinstance(message, str):
            continue
        message = ' '.join(message.casefold().split())
        if message in ('first episode free', 'watch first episode free'):
            facts.add('preview_only')
        if re.search(r'\b(?:included with|watch with|watch now with) (?:your )?prime\b', message):
            facts.add('prime_message')
        if message in ('purchased', 'you purchased this', 'you own this title'):
            facts.add('owned_message')
        if message in ('rented', 'you rented this'):
            facts.add('rental_message')
    # Look only inside explicit offers; never inherit an unrelated child's cues.
    offers = node.get('offers') or []
    if isinstance(offers, dict):
        offers = list(offers.values())
    for offer in offers if isinstance(offers, list) else []:
        if not isinstance(offer, dict):
            continue
        if str(offer.get('offerClassification') or '').upper() == 'PRIME':
            facts.add('prime_offer')
        if str(offer.get('benefitId') or '').casefold() == 'prime':
            facts.add('prime_offer')
        if offer.get('isPurchased') is True or offer.get('isOwned') is True:
            facts.add('owned_offer')
    return ','.join(sorted(facts))


def prime_included(evidence):
    values = set(str(evidence or '').split(','))
    if 'preview_only' in values and not values.intersection(
            ('prime_flag', 'prime_classification', 'prime_offer', 'prime_benefit', 'prime_message')):
        return False
    return any(value.startswith('prime_') for value in values)


def merge_evidence(first, second):
    return ','.join(sorted(set(filter(None, str(first or '').split(',') + str(second or '').split(',')))))
