"""`cnfut/data/contracts.py` 的手算测试（R2 数据回填的合约代码规则）。

规则来自 `SETUP_DONE.md` §2：SHFE/DCE 用 4 位 `YYMM`、CZCE 用 3 位 `YMM`，且老合约必须
按代码生成后直接订阅（`query_quotes` 列表被截断，B1）。
"""

import pandas as pd
import pytest

from cnfut.data.contracts import (contract_code, delivery_months, last_hold_date,
                                  plan_contracts, symbol_digits)

SESSIONS = pd.bdate_range("2016-06-01", "2019-12-31")


def test_contract_code_four_digit_exchanges():
    assert contract_code("SHFE", "rb", 2018, 1) == "SHFE.rb1801"
    assert contract_code("DCE", "m", 2017, 12) == "DCE.m1712"
    assert contract_code("dce", "y", 2020, 5) == "DCE.y2005"


def test_contract_code_czce_three_digit():
    """郑商所 3 位年份：2018-01 → 801、2019-12 → 912（`TA601` 是 2026-01，见 SETUP_DONE §1）。"""
    assert contract_code("CZCE", "TA", 2018, 1) == "CZCE.TA801"
    assert contract_code("CZCE", "SA", 2019, 12) == "CZCE.SA912"
    assert contract_code("CZCE", "TA", 2026, 1) == "CZCE.TA601"


def test_contract_code_rejects_unknown_exchange_and_bad_month():
    with pytest.raises(ValueError):
        contract_code("CFFEX", "IF", 2018, 1)
    with pytest.raises(ValueError):
        contract_code("SHFE", "rb", 2018, 13)


def test_symbol_digits_and_delivery_months():
    symbols = ["SHFE.rb1801", "SHFE.rb1805", "SHFE.rb1810", "DCE.m1801", "CZCE.TA601"]
    assert symbol_digits("CZCE.TA601") == "601"
    assert symbol_digits("SHFE.rb1801") == "1801"
    assert delivery_months(symbols, "rb") == [1, 5, 10]
    assert delivery_months(symbols, "m") == [1]
    assert delivery_months(symbols, "TA") == [1]
    assert delivery_months(symbols, "zz") == []


def test_last_hold_date_is_previous_month_last_session():
    # 2018-01 交割 → 2017-12 的最后一个交易日
    assert last_hold_date(2018, 1, SESSIONS) == pd.Timestamp("2017-12-29")
    # 2017-11 交割 → 2017-10-31（2017-10-01~08 为国庆假期，用工作日序列不影响结论）
    assert last_hold_date(2017, 11, SESSIONS).month == 10


def test_last_hold_date_requires_calendar_coverage():
    with pytest.raises(ValueError):
        last_hold_date(2015, 6, SESSIONS)


def test_plan_contracts_skips_months_outside_calendar():
    """交易日历不覆盖的交割月不能算 limit_date，必须跳过而不是抛错（脚本会喂多年的年份）。"""
    plan = plan_contracts("SHFE", "rb", [12], [2015, 2017], SESSIONS,
                          "2017-01-01", "2017-12-31")
    assert [row["symbol"] for row in plan] == ["SHFE.rb1712"]


def test_plan_contracts_filters_by_limit_window():
    plan = plan_contracts("SHFE", "rb", [1, 5], [2017, 2018], SESSIONS,
                          "2017-09-01", "2018-03-31")
    symbols = [row["symbol"] for row in plan]
    # 2018-01 交割（limit 2017-12-29）与 2018-05 交割（limit 2018-04-27，额外 120 天覆盖）
    assert "SHFE.rb1801" in symbols
    assert "SHFE.rb1701" not in symbols          # 2016-12 到期，早于 start
    assert all(row["limit_date"] >= pd.Timestamp("2017-09-01") for row in plan)
    assert plan == sorted(plan, key=lambda row: (row["delivery_year"], row["delivery_month"]))

    rb1801 = {row["symbol"]: row for row in plan}["SHFE.rb1801"]
    assert rb1801["delivery_month"] == 1
    assert rb1801["limit_date"] == pd.Timestamp("2017-12-29")
