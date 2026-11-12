from textutil import fold, phone_keys


def test_fold_ignora_acento_e_caixa():
    assert fold("João") == fold("JOAO") == "joao"


def test_phone_keys_nono_digito():
    assert phone_keys("5589994127883") == {"5589994127883", "558994127883"}
    assert phone_keys("558994127883") == {"558994127883", "5589994127883"}


def test_fold_nao_ascii():
    assert fold("Ação ÇÃO ñandú") == "acao cao nandu"
    assert fold("日本語") == "日本語"
    assert fold(None) == "" and fold("") == ""
