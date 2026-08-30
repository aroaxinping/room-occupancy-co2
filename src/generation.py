"""How much CO2 a specific person exhales, from body size and activity.

The model treats `G` as a constant taken from a table, and the sensitivity
analysis in the README shows that assumption dominates its error. It does not
have to be a constant: CO2 output follows from metabolic rate, which follows
from sex, age, body mass and how hard someone is working.

This module implements the method published in

    Persily, A., & de Jonge, L. (2017). Carbon dioxide generation rates for
    building occupants. Indoor Air, 27(5), 868-879.
    https://doi.org/10.1111/ina.12383

Their Equation 8, with V_CO2 in L/s, BMR in MJ/day and the level of physical
activity M in met:

    V_CO2 = RQ * BMR * M * 0.000569

and, with RQ fixed at 0.85, their Equation 9:

    V_CO2 = BMR * M * 0.000484

Both are stated at an air pressure of 101 kPa and a temperature of 273 K. For
other conditions the paper gives Equation 10 and Equation 11, with T in K and
P in kPa:

    V_CO2 = RQ * BMR * M * (T/P) * 0.000211
    V_CO2 = BMR * M * (T/P) * 0.000179

BMR comes from the paper's Table 1, which reproduces Schofield (1985) as
adopted by FAO/WHO/UNU, in MJ/day from body mass in kg.

`docs/generation-rate.md` records how this was checked against the paper and
where an earlier first-principles derivation in this file diverged from it;
`tests/test_generation.py` asserts the published numbers.

This is deliberately the *forward* direction. Running it backwards to infer a
person's sex or mass from their CO2 does not work: activity swings output by
about 100% while sex and mass account for 20-35%, so the confounder is larger
than the signal. The README's sensitivity table shows why. What this is for is
predicting what a known occupant should produce, so that a measurement has
something to be checked against.
"""
from dataclasses import dataclass

# Litres of CO2 exhaled per litre of O2 consumed. Persily & de Jonge use a
# single value of 0.85 for a mixed diet, derived from NHANES macronutrient
# intake via their Equation 7 (0.84 men, 0.86 women); 0.7 is pure fat, 1.0
# pure carbohydrate.
RESPIRATORY_QUOTIENT = 0.85

# Litres of O2 consumed per second, per MJ/day of energy expenditure, at
# 101 kPa and 273 K. This is the 0.000569 in the paper's Equation 8. It comes
# from their stated conversion of 1 kcal (0.0042 MJ) of energy use to 0.206 L
# of oxygen, i.e. about 49 L O2 per MJ.
O2_LITRES_PER_SECOND_PER_MJ_DAY = 0.000569

# The same conversion expressed as an energy equivalent of oxygen, for
# readability: about 20.3 kJ released per litre of O2 oxidised. Note this is
# the mixed-diet figure consistent with RQ = 0.85. The commonly quoted 20.9
# kJ/L corresponds to an RQ near 0.96; using it overstates the energy per
# litre by about 2.7% and so understates CO2 output by the same amount.
KJ_PER_LITRE_O2 = 1000.0 / (O2_LITRES_PER_SECOND_PER_MJ_DAY * 86400.0)

# Reference conditions for Equations 8 and 9.
REFERENCE_TEMPERATURE_K = 273.0
REFERENCE_PRESSURE_KPA = 101.0

# Persily & de Jonge Table 1: Schofield BMR in MJ/day from body mass in kg,
# as (slope, intercept) per (sex, age_from, age_to).
SCHOFIELD_MJ_DAY = {
    ("m", 0, 3): (0.249, -0.127),
    ("m", 3, 10): (0.095, 2.110),
    ("m", 10, 18): (0.074, 2.754),
    ("m", 18, 30): (0.063, 2.896),
    ("m", 30, 60): (0.048, 3.653),
    ("m", 60, 200): (0.049, 2.459),
    ("f", 0, 3): (0.244, -0.130),
    ("f", 3, 10): (0.085, 2.033),
    ("f", 10, 18): (0.056, 2.898),
    ("f", 18, 30): (0.062, 2.036),
    ("f", 30, 60): (0.034, 3.538),
    ("f", 60, 200): (0.038, 2.755),
}

# Activity multipliers, in met. The paper publishes two sources that do not
# agree with each other: FAO physical activity ratios (its Table 2) and the
# compendium of physical activities (its Table 3). Each value below is
# labelled with where it comes from; where both tables speak, they bracket
# the figure used here.
METS = {
    "resting": 1.0,             # Table 2 sleeping 1.0; Table 3 lying or
                                # sitting quietly 1.0-1.3
    "seated quiet": 1.2,        # Table 2 sitting quietly, 1.2 both sexes
    "desk work": 1.4,           # between Table 2 office worker sitting at
                                # desk 1.3 and Table 3 office work 1.5
    "office work": 1.5,         # Table 3 "sitting tasks, light effort
                                # (e.g, office work)"; the paper's own
                                # worked example uses this value
    "talking, seated": 1.6,     # not published in either table; retained
                                # from the earlier version of this module
    "standing, light": 2.0,     # between Table 2 standing 1.4-1.5 and
                                # Table 3 standing tasks, light effort 3.0
}


def co2_litres_per_second(bmr_mj_day: float, met: float,
                          respiratory_quotient: float = RESPIRATORY_QUOTIENT,
                          temperature_k: float = REFERENCE_TEMPERATURE_K,
                          pressure_kpa: float = REFERENCE_PRESSURE_KPA
                          ) -> float:
    """Persily & de Jonge Equation 8, generalised to Equation 10 for T and P.

    At the default 273 K and 101 kPa this is exactly their Equation 8,
    V_CO2 = RQ * BMR * M * 0.000569, in L/s.
    """
    litres_o2 = bmr_mj_day * met * O2_LITRES_PER_SECOND_PER_MJ_DAY
    density = (temperature_k / REFERENCE_TEMPERATURE_K) * (
        REFERENCE_PRESSURE_KPA / pressure_kpa)
    return litres_o2 * respiratory_quotient * density


@dataclass
class Person:
    sex: str          # "m" or "f"
    age: int
    mass_kg: float

    def bmr_mj_day(self) -> float:
        """BMR in MJ/day, Persily & de Jonge Table 1 (Schofield)."""
        for (sex, lo, hi), (slope, intercept) in SCHOFIELD_MJ_DAY.items():
            if sex == self.sex and lo <= self.age < hi:
                return slope * self.mass_kg + intercept
        raise ValueError(f"no Schofield band for {self.sex}, age {self.age}")

    def bmr_kj_day(self) -> float:
        return self.bmr_mj_day() * 1000.0

    def co2_litres_per_second(self, activity: str = "desk work",
                              temperature_k: float = REFERENCE_TEMPERATURE_K,
                              pressure_kpa: float = REFERENCE_PRESSURE_KPA
                              ) -> float:
        """CO2 generation rate in L/s, at the given air conditions.

        Defaults to the paper's reference 273 K and 101 kPa, so the result is
        directly comparable with its Table 4.
        """
        return co2_litres_per_second(self.bmr_mj_day(), METS[activity],
                                     RESPIRATORY_QUOTIENT,
                                     temperature_k, pressure_kpa)

    def co2_litres_per_hour(self, activity: str = "desk work",
                            temperature_k: float = REFERENCE_TEMPERATURE_K,
                            pressure_kpa: float = REFERENCE_PRESSURE_KPA
                            ) -> float:
        """As `co2_litres_per_second`, in L/h.

        Note the default conditions are the paper's 273 K, not room
        temperature. A volumetric rate is only meaningful with its air
        conditions attached; `steady_state_ppm` supplies room conditions.
        """
        return 3600.0 * self.co2_litres_per_second(activity, temperature_k,
                                                   pressure_kpa)


def steady_state_ppm(people, volume_m3: float, ach: float,
                     activity: str = "desk work",
                     temperature_k: float = 293.15,
                     pressure_kpa: float = REFERENCE_PRESSURE_KPA) -> float:
    """The CO2 excess a group holds a room at, once it settles.

    Rearranged from the mass balance with dC/dt = 0, this is what the
    measurement in the README compares against: an occupancy step of known size
    raises the equilibrium by a predictable amount, given a measured ventilation
    rate.

    ppm is a volume fraction, so the exhaled volume and the room volume have to
    be at the same air conditions. The default here is room temperature, not
    the paper's 273 K reference; at 293.15 K the generation rate is 7.4% higher
    than the Table 4 value, and so is the resulting ppm.
    """
    total = sum(p.co2_litres_per_hour(activity, temperature_k, pressure_kpa)
                for p in people) / 1000  # m3/h
    return total * 1e6 / (ach * volume_m3)


if __name__ == "__main__":
    print("check against the tabulated figure the model assumes (18 L/h):")
    for label, person in [("f 30, 60 kg", Person("f", 30, 60)),
                          ("f 30, 70 kg", Person("f", 30, 70)),
                          ("m 30, 75 kg", Person("m", 30, 75)),
                          ("m 30, 90 kg", Person("m", 30, 90))]:
        rates = {a: person.co2_litres_per_hour(a, temperature_k=293.15)
                 for a in METS}
        print(f"  {label}: BMR {person.bmr_mj_day():.1f} MJ/day | "
              + "  ".join(f"{a} {r:.0f}" for a, r in rates.items())
              + " L/h at 293 K")
