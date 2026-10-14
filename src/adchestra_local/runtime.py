"""Single-account local runtime."""
_config = None

def set_default_config(config):
    global _config
    _config = config

def current_config():
    if _config is None:
        raise RuntimeError("Run adchestra-local setup first.")
    return _config

def current_tenant():
    return "local"

def ads_login_customer_id(customer_id):
    if _config is None:
        return ""
    config = current_config()
    cid = customer_id.replace("-", "") or config.ads.customer_id
    if cid not in {config.ads.customer_id.replace("-", ""), config.ads.login_customer_id.replace("-", "")}:
        raise ValueError("This account is not configured. Run setup with its advertiser ID.")
    return config.ads.login_customer_id
