"""Separate the certificate message envelope from its signed certificate.

The signed certificate and its signature are preserved byte-for-byte. Only the
public transport envelope is removed for the CDM's SetServerCertificate input.
"""


def _fields(data):
    if not 0<len(data)<=65536:raise ValueError('Invalid Widevine certificate size')
    offset=0;values={};count=0
    def integer():
        nonlocal offset
        result=0
        for shift in range(0,70,7):
            if offset>=len(data):raise ValueError('Truncated certificate protocol integer')
            byte=data[offset];offset+=1;result|=(byte&127)<<shift
            if not byte&128:return result
        raise ValueError('Certificate integer exceeds its limit')
    while offset<len(data):
        count+=1
        if count>1000:raise ValueError('Certificate has too many protocol fields')
        tag=integer();number,wire=tag>>3,tag&7
        if not number:raise ValueError('Invalid certificate field')
        if wire==0:value=integer()
        elif wire in (1,2,5):
            size=integer() if wire==2 else 8 if wire==1 else 4
            end=offset+size
            if end>len(data):raise ValueError('Truncated certificate protocol field')
            value=data[offset:end];offset=end
        else:raise ValueError('Unsupported certificate protocol field')
        values.setdefault(number,[]).append((wire,value))
    return values


def cdm_certificate(data):
    """Return SignedDrmCertificate bytes and safe framing facts, never proof data."""
    fields=_fields(data);wrapped=fields.get(1)==[(0,5)]
    if wrapped:
        message=fields.get(2,[])
        if len(message)!=1 or message[0][0]!=2:raise ValueError('Certificate message has no signed certificate')
        payload=message[0][1];certificate=_fields(payload)
    else:payload=data;certificate=fields
    for number in (1,2):
        value=certificate.get(number,[])
        if len(value)!=1 or value[0][0]!=2 or not value[0][1]:
            raise ValueError('Widevine certificate has no certificate data or signature')
    return payload,{'provider_certificate_bytes':len(data),'cdm_certificate_bytes':len(payload),
                    'provider_format':'signed_message' if wrapped else 'signed_drm_certificate',
                    'cdm_format':'signed_drm_certificate','signed_certificate_bytes_preserved':True,
                    'cryptographic_verification_owner':'widevine_cdm'}
