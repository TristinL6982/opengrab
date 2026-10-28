import tempfile
from unittest.mock import Mock

from src.opengrab.captcha.chain import SolverChain
from src.opengrab.settings import Settings


def test_solver_chain_ordered():
    # Create a mock settings object with fake keys for paid solvers
    settings = Settings()
    settings.captcha_2captcha_key = "fake_2captcha_key"
    settings.captcha_capmonster_key = "fake_capmonster_key"
    settings.captcha_capsolver_key = "fake_capsolver_key"
    
    # Create SolverChain instance
    chain = SolverChain(settings)
    
    # Test for turnstile (token-based captchas) - manual should be last
    ordered_turnstile = chain._ordered("turnstile")
    ids_turnstile = [s.id for s in ordered_turnstile]
    
    # Paid solvers should come before manual
    assert "manual" == ids_turnstile[-1]  # manual is last
    assert "2captcha" in ids_turnstile
    assert "capmonster" in ids_turnstile
    assert "capsolver" in ids_turnstile
    
    # Test for text captcha - ddddocr should be first (if enabled) and manual last
    ordered_text = chain._ordered("text")
    ids_text = [s.id for s in ordered_text]
    
    # ddddocr is probably first, manual is last
    if len(ids_text) > 0:
        assert "manual" == ids_text[-1]  # manual is always last
    
    # Test with explicit manual preference - manual should be first
    settings.captcha_provider = "manual"
    chain_manual = SolverChain(settings)
    ordered_manual = chain_manual._ordered("turnstile")
    ids_manual = [s.id for s in ordered_manual]
    
    assert ids_manual[0] == "manual"  # manual is first when explicitly preferred
    
    # Test with explicit 2captcha preference
    settings.captcha_provider = "2captcha"
    chain_2captcha = SolverChain(settings)
    ordered_2captcha = chain_2captcha._ordered("turnstile")
    ids_2captcha = [s.id for s in ordered_2captcha]
    
    assert ids_2captcha[0] == "2captcha"  # 2captcha is first when explicitly preferred
    assert "manual" == ids_2captcha[-1]  # manual is still last
    
    # Cleanup - reset settings to avoid side effects
    settings.captcha_provider = None


def test_solver_chain_ordered_with_disabled_solvers():
    # Test with no keys configured (all paid solvers disabled)
    settings = Settings()
    # Do not set any captcha keys - they should be disabled
    
    chain = SolverChain(settings)
    
    # With no keys, only ddddocr and manual should be enabled, in that order
    ordered_turnstile = chain._ordered("turnstile")
    ids_turnstile = [s.id for s in ordered_turnstile]
    
    assert len(ids_turnstile) >= 2  # Should have at least ddddocr and manual
    
    # manual should always be last
    assert "manual" == ids_turnstile[-1]
    
    # Test that when there are only non-paid solvers, the order is correct
    ordered_text = chain._ordered("text")
    ids_text = [s.id for s in ordered_text]
    
    if len(ids_text) > 0:
        assert "manual" == ids_text[-1]