"""Check src/generation.py against the numbers Persily & de Jonge published.

Source of every asserted value:

    Persily, A., & de Jonge, L. (2017). Carbon dioxide generation rates for
    building occupants. Indoor Air, 27(5), 868-879. doi:10.1111/ina.12383

Open access under CC-BY; the NIST public copy used here is
https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=920870

Nothing in this file is an invented figure. Each block cites the table,
equation or sentence of the paper it came from.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.generation import (  # noqa: E402
    METS,
    O2_LITRES_PER_SECOND_PER_MJ_DAY,
    RESPIRATORY_QUOTIENT,
    Person,
    co2_litres_per_second,
    steady_state_ppm,
)

# The paper prints Table 4 to four decimal places, so the finest resolution
# any assertion against it can claim is 0.0001 L/s. That is the tolerance used
# throughout for table cells.
TABLE_RESOLUTION_L_S = 1e-4


# --------------------------------------------------------------------------
# Equations 8 and 9, as published (paper p. 874).
#   Eq 8:  V_CO2 = RQ * BMR * M * 0.000569        (L/s, 273 K, 101 kPa)
#   Eq 9:  V_CO2 = BMR * M * 0.000484             (as above, with RQ = 0.85)
# --------------------------------------------------------------------------

def test_equation_8_constant_is_the_published_one():
    assert O2_LITRES_PER_SECOND_PER_MJ_DAY == 0.000569
    assert RESPIRATORY_QUOTIENT == 0.85


def test_equation_9_is_equation_8_with_rq_085():
    # The paper rounds RQ * 0.000569 = 0.00048365 to the 0.000484 of Eq 9.
    assert round(RESPIRATORY_QUOTIENT * O2_LITRES_PER_SECOND_PER_MJ_DAY, 6) == 0.000484


def test_equation_8_matches_equation_9_within_its_rounding():
    # Eq 9's rounded constant and Eq 8's exact product must agree to better
    # than the resolution of Table 4 across the table's whole range.
    for bmr in (1.75, 5.19, 8.24):
        for met in (1.0, 2.0, 4.0):
            eq8 = co2_litres_per_second(bmr, met)
            eq9 = bmr * met * 0.000484
            assert eq8 == pytest.approx(eq9, abs=TABLE_RESOLUTION_L_S)


# --------------------------------------------------------------------------
# Table 1 (paper p. 871): Schofield BMR, MJ/day from body mass in kg.
# The paper states two BMR values in prose on p. 871:
#   "the BMR for an 85-kg male between 30 and 60 year old is 7.73 MJ/day
#    (89.5 W) and 6.09 MJ/day (70.5 W) for a 75-kg female in this same age
#    range"
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sex,age,mass,bmr_mj_day", [
    ("m", 45, 85.0, 7.73),   # paper p. 871, prose
    ("f", 45, 75.0, 6.09),   # paper p. 871, prose
])
def test_bmr_prose_examples(sex, age, mass, bmr_mj_day):
    assert Person(sex, age, mass).bmr_mj_day() == pytest.approx(
        bmr_mj_day, abs=0.005)  # paper quotes BMR to 2 decimal places


# Table 4 (paper pp. 874-875) prints the mean body mass and the resulting BMR
# for each age band. Verifying our Table 1 coefficients against that column
# checks the Schofield equations independently of the CO2 conversion.
# Columns: sex, representative age in band, mean body mass (kg), BMR (MJ/day).
TABLE_4_BMR = [
    ("m", 25, 84.9, 8.24), ("m", 35, 87.0, 7.83), ("m", 45, 90.5, 8.00),
    ("m", 55, 89.5, 7.95), ("m", 65, 89.5, 6.84), ("m", 75, 83.9, 6.57),
    ("m", 85, 76.1, 6.19),
    ("f", 25, 71.9, 6.49), ("f", 35, 74.8, 6.08), ("f", 45, 77.1, 6.16),
    ("f", 55, 77.5, 6.17), ("f", 65, 76.8, 5.67), ("f", 75, 70.8, 5.45),
    ("f", 85, 64.1, 5.19),
]


@pytest.mark.parametrize("sex,age,mass,bmr_mj_day", TABLE_4_BMR)
def test_table_4_bmr_column(sex, age, mass, bmr_mj_day):
    assert Person(sex, age, mass).bmr_mj_day() == pytest.approx(
        bmr_mj_day, abs=0.005)  # Table 4 quotes BMR to 2 decimal places


# --------------------------------------------------------------------------
# Table 4 (paper pp. 874-875): CO2 generation rates in L/s at 273 K and
# 101 kPa, for levels of physical activity M = 1.0, 1.2, 1.4, 1.6, 2.0, 3.0
# and 4.0 met, based on the mean body mass in each age group.
# Only the adult rows are transcribed here; those are the rows this project
# uses. Values are exactly as printed.
# --------------------------------------------------------------------------

TABLE_4_METS = [1.0, 1.2, 1.4, 1.6, 2.0, 3.0, 4.0]

TABLE_4_RATES = [
    # sex, representative age, mean body mass kg, rates across TABLE_4_METS
    ("m", 25, 84.9, [0.0039, 0.0048, 0.0056, 0.0064, 0.0080, 0.0120, 0.0160]),
    ("m", 35, 87.0, [0.0037, 0.0046, 0.0053, 0.0061, 0.0076, 0.0114, 0.0152]),
    ("m", 45, 90.5, [0.0038, 0.0046, 0.0054, 0.0062, 0.0077, 0.0116, 0.0155]),
    ("m", 55, 89.5, [0.0038, 0.0046, 0.0054, 0.0062, 0.0077, 0.0116, 0.0154]),
    ("m", 65, 89.5, [0.0033, 0.0040, 0.0046, 0.0053, 0.0066, 0.0099, 0.0133]),
    ("m", 75, 83.9, [0.0031, 0.0038, 0.0045, 0.0051, 0.0064, 0.0095, 0.0127]),
    ("m", 85, 76.1, [0.0030, 0.0036, 0.0042, 0.0048, 0.0060, 0.0090, 0.0120]),
    ("f", 25, 71.9, [0.0031, 0.0038, 0.0044, 0.0050, 0.0063, 0.0094, 0.0126]),
    ("f", 35, 74.8, [0.0029, 0.0035, 0.0041, 0.0047, 0.0059, 0.0088, 0.0118]),
    ("f", 45, 77.1, [0.0029, 0.0036, 0.0042, 0.0048, 0.0060, 0.0090, 0.0119]),
    ("f", 55, 77.5, [0.0030, 0.0036, 0.0042, 0.0048, 0.0060, 0.0090, 0.0120]),
    ("f", 65, 76.8, [0.0027, 0.0033, 0.0038, 0.0044, 0.0055, 0.0082, 0.0110]),
    ("f", 75, 70.8, [0.0026, 0.0032, 0.0037, 0.0042, 0.0053, 0.0079, 0.0106]),
    ("f", 85, 64.1, [0.0025, 0.0030, 0.0035, 0.0040, 0.0050, 0.0075, 0.0101]),
]


@pytest.mark.parametrize("sex,age,mass,rates", TABLE_4_RATES)
def test_table_4_generation_rates(sex, age, mass, rates):
    person = Person(sex, age, mass)
    for met, published in zip(TABLE_4_METS, rates):
        computed = co2_litres_per_second(person.bmr_mj_day(), met)
        assert computed == pytest.approx(published, abs=TABLE_RESOLUTION_L_S), (
            f"{sex} age {age} at {met} met: got {computed:.5f}, "
            f"Table 4 prints {published}")


# --------------------------------------------------------------------------
# Worked example, paper p. 874:
#   "A BMR value of 7.73 MJ/day ... therefore corresponds to 0.0037 L/s of
#    CO2 production. Using the physical activity level of 1.5 met for
#    'sitting tasks, light effort (eg, office work)' in Table 3 results in a
#    CO2 generation rate of 0.0056 L/s"
# --------------------------------------------------------------------------

def test_worked_example_85kg_male_at_rest():
    person = Person("m", 45, 85.0)          # BMR 7.73 MJ/day, paper p. 871
    assert person.co2_litres_per_second("resting") == pytest.approx(
        0.0037, abs=TABLE_RESOLUTION_L_S)


def test_worked_example_85kg_male_office_work():
    person = Person("m", 45, 85.0)
    assert METS["office work"] == 1.5       # paper Table 3
    assert person.co2_litres_per_second("office work") == pytest.approx(
        0.0056, abs=TABLE_RESOLUTION_L_S)


# --------------------------------------------------------------------------
# Intermediate conversions stated in prose, paper p. 874:
#   "1 MJ/day of energy use corresponding to 0.00057 L/s of oxygen
#    consumption, which based on a respiratory quotient of 0.85 ...
#    corresponds to 0.00048 L/s of CO2 production"
# --------------------------------------------------------------------------

def test_one_mj_per_day_of_oxygen_and_co2():
    assert O2_LITRES_PER_SECOND_PER_MJ_DAY == pytest.approx(
        0.00057, abs=5e-6)                                   # quoted to 5 d.p.
    assert co2_litres_per_second(1.0, 1.0) == pytest.approx(
        0.00048, abs=5e-6)


# --------------------------------------------------------------------------
# Equations 10 and 11 (paper p. 874), the temperature/pressure form:
#   Eq 10: V_CO2 = RQ * BMR * M * (T/P) * 0.000211
#   Eq 11: V_CO2 = BMR * M * (T/P) * 0.000179     (RQ = 0.85)
# At T = 273 K and P = 101 kPa these must collapse onto Equations 8 and 9.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("temperature_k", [273.0, 293.15, 300.0])
def test_equation_10_temperature_pressure_form(temperature_k):
    bmr, met, pressure = 7.73, 1.5, 101.0
    published = (RESPIRATORY_QUOTIENT * bmr * met
                 * (temperature_k / pressure) * 0.000211)
    computed = co2_litres_per_second(bmr, met, temperature_k=temperature_k,
                                     pressure_kpa=pressure)
    # 0.000211 is 0.000569 * (101 / 273) rounded to three significant figures;
    # 0.000211 * 273 / 101 = 0.00057033, 0.23% above 0.000569. The two
    # published forms therefore agree only to about that precision.
    assert computed == pytest.approx(published, rel=3e-3)


def test_reference_conditions_collapse_to_equation_8():
    assert co2_litres_per_second(7.73, 1.5, temperature_k=273.0,
                                 pressure_kpa=101.0) == pytest.approx(
        0.85 * 7.73 * 1.5 * 0.000569, rel=1e-12)


# --------------------------------------------------------------------------
# Regression guard: the superseded first-principles chain in this module used
# 20.9 kJ per litre of O2. The paper's conversion (1 kcal -> 0.206 L O2) is
# equivalent to about 20.3 kJ/L. The difference is small but systematic, and
# it is the reason Table 4 could not be reproduced before. This test pins the
# direction and rough size so the old constant cannot creep back in.
# --------------------------------------------------------------------------

def test_superseded_20_9_constant_undershoots_table_4():
    bmr, met = 8.24, 4.0        # Table 4, male 21 to <30, at 4.0 met
    published = 0.0160          # Table 4, that cell
    old_chain = (bmr * met * 1000.0 / 20.9) * RESPIRATORY_QUOTIENT / 86400.0
    # The old chain misses this cell by ~0.00047 L/s, several times the
    # table's own resolution; the paper's constant lands inside it.
    assert published - old_chain > 4 * TABLE_RESOLUTION_L_S
    assert co2_litres_per_second(bmr, met) == pytest.approx(
        published, abs=TABLE_RESOLUTION_L_S)
    assert co2_litres_per_second(bmr, met) / old_chain == pytest.approx(
        1.027, abs=0.002)


# --------------------------------------------------------------------------
# The room mass balance is this project's own arithmetic, not the paper's.
# Check it against a hand-computable case rather than a published value.
# --------------------------------------------------------------------------

def test_steady_state_ppm_is_generation_over_ventilation():
    person = Person("m", 45, 85.0)
    volume_m3, ach = 40.0, 0.5
    rate_m3_h = person.co2_litres_per_hour(
        "office work", temperature_k=293.15) / 1000.0
    expected = rate_m3_h * 1e6 / (ach * volume_m3)
    assert steady_state_ppm([person], volume_m3, ach,
                            "office work") == pytest.approx(expected)


def test_steady_state_ppm_uses_room_temperature_by_default():
    # ppm is a volume fraction, so the exhaled volume must be at room
    # conditions, not the paper's 273 K reference. 293.15/273 = 1.0738.
    person = Person("m", 45, 85.0)
    at_room = steady_state_ppm([person], 40.0, 0.5)
    at_reference = steady_state_ppm([person], 40.0, 0.5, temperature_k=273.0)
    assert at_room / at_reference == pytest.approx(293.15 / 273.0, rel=1e-9)
