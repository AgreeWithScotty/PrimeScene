"""Automatic resolution policy; never changes CDM security or provider rights."""
QUALITY_POLICY='automatic-best-v1'


def preferred_quality(hardware_level):
    return 'UHD' if hardware_level=='L1' else 'HD'


def permitted_quality(codes):
    """Interpret explicit provider quality denials, most restrictive first."""
    if any(('Widevine' in code and 'HdContentNotAllowed' in code) or
           code=='Downgrade.Sd' or code.endswith('.VideoDefinitionDowngrade.Sd') for code in codes):
        return 'SD'
    if any(('Widevine' in code and 'UhdContentNotAllowed' in code) or
           code=='Downgrade.Hd' or code.endswith('.VideoDefinitionDowngrade.Hd') for code in codes):
        return 'HD'
    return None


def lower_quality(requested,permitted):
    """A retry must strictly decrease quality; SD and non-quality errors stop."""
    if requested=='UHD' and permitted in ('HD','SD'):return permitted
    if requested=='HD' and permitted=='SD':return 'SD'
    return None
