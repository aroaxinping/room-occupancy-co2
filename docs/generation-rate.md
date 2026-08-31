# Where the CO2 generation rate comes from

This note exists so the constants in `src/generation.py` can be checked against
a primary source rather than taken on trust. `tests/test_generation.py` asserts
the published numbers cited below; run it with `pytest tests/test_generation.py`.

## Citation

Persily, A., & de Jonge, L. (2017). Carbon dioxide generation rates for
building occupants. *Indoor Air*, 27(5), 868-879.
<https://doi.org/10.1111/ina.12383>

Open access under CC-BY. Verified against the NIST-hosted copy at
<https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=920870>, with the
Healthy Buildings 2017 Europe conference version
(<https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=922955>, paper 0242)
used as a cross-check. Both were downloaded and their text extracted locally;
the constants below agree between the two.

## The equation as published

The paper's Equation 8, with V_CO2 in L/s, BMR in MJ/day, the level of physical
activity M in met, and RQ the respiratory quotient:

    V_CO2 = RQ * BMR * M * 0.000569

With RQ fixed at 0.85, its Equation 9:

    V_CO2 = BMR * M * 0.000484

Both hold at an air pressure of 101 kPa and a temperature of 273 K. Equations
10 and 11 generalise them, with T in K and P in kPa:

    V_CO2 = RQ * BMR * M * (T/P) * 0.000211
    V_CO2 = BMR * M * (T/P) * 0.000179

Where the constant comes from: the paper converts 1 kcal (0.0042 MJ) of energy
expenditure to 0.206 L of oxygen consumed, giving 1 MJ/day of energy use as
0.00057 L/s of oxygen, and hence 0.00048 L/s of CO2 at RQ = 0.85.

RQ = 0.85 is the paper's single mixed-diet value. It is derived from NHANES
macronutrient intake through its Equation 7 (a food-quotient formula), which
yields 0.84 for men and 0.86 for women.

BMR comes from the paper's Table 1, which reproduces the Schofield (1985)
equations as adopted by FAO/WHO/UNU: BMR in MJ/day as a linear function of body
mass in kg, per sex and age band.

The paper's own worked example: an 85 kg male aged 30-60 has a BMR of
7.73 MJ/day, giving 0.0037 L/s of CO2 at rest and 0.0056 L/s at the 1.5 met it
lists for "sitting tasks, light effort (e.g, office work)". Its Table 4
tabulates rates for both sexes across thirteen age bands and seven met levels.

## What this repository derived independently, and how it compared

Before the paper was obtained, `src/generation.py` built the same quantity from
first principles:

    BMR (Schofield, kJ/day) x MET / 20.9 kJ per L O2 x RQ 0.85

Three of the four links were right, and match the paper exactly:

- The Schofield coefficients were identical to the paper's Table 1, once the
  unit difference is accounted for (kJ/day here, MJ/day there).
- RQ = 0.85 is the paper's value, for the reason the paper gives.
- The structure — BMR, times an activity multiplier, converted through oxygen
  consumption, times RQ — is the structure of Equation 8.

The fourth link was wrong. **20.9 kJ per litre of O2 is too high.** The paper's
conversion (1 kcal to 0.206 L O2) implies about 49.0 L of O2 per MJ, i.e. an
energy equivalent of oxygen near **20.3 kJ/L**. 20.9 kJ/L is the figure for an
RQ around 0.96, a carbohydrate-heavy diet; it is inconsistent with the RQ of
0.85 used two lines later in the same chain.

The effect is a uniform **2.7% underestimate** of CO2 output
(20.9 / 20.34 = 1.027). Concretely, against the paper's worked example:

| BMR 7.73 MJ/day | paper | old chain here | error |
|---|---|---|---|
| 1.0 met | 0.0037 L/s | 0.00364 L/s | -2.7% |
| 1.5 met | 0.0056 L/s | 0.00546 L/s | -2.7% |

Across every adult cell of the paper's Table 4, the old chain missed by up to
0.00048 L/s — several times the 0.0001 L/s resolution at which the table is
printed. The paper's constant reproduces every one of those cells to within
0.0001 L/s. So the derivation did not survive: the paper is right, and
`src/generation.py` has been changed to use its constant.

A second, separate problem: the paper's rates are volumetric **at 273 K and
101 kPa**, and the old `steady_state_ppm` compared them against a room volume
at room temperature. Because ppm is a volume fraction, both volumes must be at
the same conditions. At 293.15 K the generation rate is 7.4% higher than the
Table 4 value, so the old code understated steady-state ppm by that much on top
of the 2.7%. `steady_state_ppm` now defaults to 293.15 K and applies the paper's
Equation 10 density correction; `co2_litres_per_second` still defaults to the
paper's 273 K reference so that its output is directly comparable with Table 4.

Combined, the two corrections raise predicted steady-state ppm by about 10%
relative to the previous version of this module.

## Two caveats about the published numbers

- **Table 4 has some last-digit rounding noise.** The male 21-to-<30 row (mean
  body mass 84.9 kg, BMR 8.24 MJ/day) prints 0.0039 L/s at 1.0 met, where
  Equation 9 gives 0.00399. The other columns of that same row are consistent
  with 0.0040 — they are exact multiples of it. Agreement with the table is
  therefore asserted at a tolerance of 0.0001 L/s, the resolution at which it
  is printed, rather than any tighter.
- **The paper misstates one body mass.** On p. 871 it gives the BMR of an 85 kg
  male aged 30-60 as 7.73 MJ/day, which the Table 1 equation confirms
  (0.048 x 85 + 3.653 = 7.73). Later on the same page it attributes that same
  7.73 MJ/day to a *75* kg male. 85 kg is the consistent reading, and is what
  the tests use.

## MET values

The paper publishes two activity-level sources that do not agree with each
other: FAO physical activity ratios (its Table 2) and the compendium of
physical activities (its Table 3). For office work, Table 2 gives 1.3 for
sitting at a desk while Table 3 gives 1.5. The `METS` dictionary in
`src/generation.py` labels each entry with the published values that bracket
it, and adds an `"office work"` key at the paper's own 1.5 so the worked
example can be reproduced. The remaining entries are unchanged from this
repository's earlier choices and are not claims about the paper; as the README's
sensitivity analysis notes, this activity level is the dominant uncertainty in
the whole calculation, larger than the constant corrected above.
