# Time in words (Rainy watch face)

The face spells the time out to the exact minute in two centred lines: the hour part on line 1, the minute part on
line 2, ending with a full stop. Examples: *Ten / oh eight.*, *Le dieci / e otto.*, *दस बजकर / आठ मिनट।*
The words come from [`time_words.json`](time_words.json). This file explains how they were chosen.

## How the face builds the two lines

For hour `h` (24h mode: 0-23; 12h mode: `h % 12`, with 0 shown as 12) and minute `m`:

1. **Exception**: if `exceptions` has the key `"<12h|24h> H:MM"` (hour not zero-padded, e.g. `"24h 0:00"`,
   `"24h 13:00"`), use its two lines. 12h keys use the 12h hour (1-12), so `"12h 1:00"` also covers 13:00, and
   `"12h 12:MM"` covers both noon and midnight unless there is a `"12h 0:MM"` for midnight, which wins.
   `generate.py` parses the numbers (`(12h|24h) (\d{1,2}):(\d{2})`), so a zero-padded `"24h 00:00"` works too.
2. **On the hour** (`m == 0`): line 1 is `hour12_at_00[h]` or `hour24_at_00[h]`, line 2 is `minute["0"]`.
   In 12h mode the midnight hour (0:00-0:59) uses key `"0"` of `hour12` / `hour12_at_00` when the language has
   one (then key `"12"` is noon only); without it, midnight reads like noon.
3. **Otherwise**: line 1 is `hour12[h]` or `hour24[h]`, line 2 is `minute[m]`.
4. Add `period` to the end of line 2 in every case, exceptions included. Exception line 2 is stored without it,
   like the minute table.

Words in `*asterisks*` carry the number and use the heavy City voice (Google Sans Flex wght 800, wdth 125, #F6EDFF). Everything else
uses the light Filler voice (wght 300, #CAC4D0). Line 1 always starts with a capital letter, and line 2 is never
empty. Apostrophes are ASCII `'`, as in the app's strings.

12h mode has no AM/PM, but the face still knows the 24-hour hour, so midnight and noon get their own words in
12h too: every language says them at 0:00 and 12:00, and Italian, French and Hindi (and English for midnight) use
them for the whole hour, as in 24h. The other hours read the same morning and evening.

Number words follow ICU spellout (`RuleBasedNumberFormat`). The exceptions are the deviations listed per
language below, and each one was checked against ICU. All strings are NFC: ș/ț use the comma below, and the
Devanagari nukta letters (ड़, ढ़) are the base letter plus U+093C, because NFC never composes them.

## Per language

### English (en)
- Line 1 is the hour, *Ten*. Line 2 is the minute: *oh eight* for :01-:09, otherwise *twenty-seven*.
- 12h: *Ten / oh eight.*, *One / oh one.*, *Twelve / thirty.* (noon hour). The midnight hour reads *Midnight /
  oh eight.* as in 24h.
- 24h: *Eighteen / oh eight.*, *Twenty-three / fifty-nine.* Hour 0 reads *Midnight / oh eight.* "Twelve oh
  eight" would be ambiguous in the mode people pick to avoid ambiguity, and "zero oh eight" isn't natural speech.
- :00 reads *Ten / o'clock.* for 1-12 in both modes. In 24h, 13:00-23:00 use the timetable reading
  *Eighteen / hundred.*, because nobody says "eighteen o'clock". 00:00 is *It's / midnight.* and 12:00 is
  *It's / noon.*, in both modes (`12h 0:00` and `12h 12:00` too). Noon's hour stays *Twelve / oh eight.*:
  "noon oh eight" isn't said.

### Italian (it)
- Line 1 is the article plus the hour: *Le dieci*, *L'una*. Line 2 is *e otto*. :00 is *Le dieci / in punto.*
- 24h: *Le diciotto / e otto.*, *Le ventuno / e ventuno.* (standard *le ventuno*; ICU's feminine gives
  *ventuna*). Hour 0 reads *Mezzanotte / e otto.* and *Mezzanotte / in punto.* Hour 12 reads *Mezzogiorno / e
  dieci.* and *Mezzogiorno / in punto.*, as Italians say it, and so does 12h (*le dodici* is never needed).
- Minutes are always numbers, so :15 and :30 read *e quindici* and *e trenta* rather than *e un quarto* or *e mezza*.
  The minute table is shared by 12h and 24h, and *le diciotto e mezza* sounds off in 24h.
- Minute 1 is *e un minuto* (*Le dieci / e un minuto.*, *L'una / e un minuto.*). A bare *e uno* sounds like
  counting. From :02 up the bare number is idiomatic: *e due*, *e ventuno*, *e trentuno*.

### French (fr)
- Line 1: *Dix heures*, *Une heure*. Line 2 is the bare minute, feminine: *huit*, *une*, *vingt et une*.
- :00 is *Dix heures / pile.* *Pile* never changes form. *Précises* would have to agree with *heure*, *midi*
  and *minuit*.
- 24h: *Dix-huit heures / huit.*, *Vingt et une heures / vingt et une.* Hour 0 reads *Minuit / huit.* and hour 12
  reads *Midi / huit.*, which is how 24-hour French is spoken. *Zéro heure* and *douze heures* are for official announcements.
- 12h does the same: *Minuit / huit.*, *Midi / pile.* (*douze heures* is never needed).
- Spelling: traditional *vingt et une*. ICU uses the 1990 spelling *vingt-et-une*.

### Spanish (es)
- Line 1: *Las diez*, *La una*. Line 2: *y ocho*. :00 is *Las diez / en punto.*
- Hours are feminine (*horas*), so 24h 21 is *Las veintiuna*. Minutes are masculine (*minutos*): *y veintiuno*,
  *y treinta y uno*. Minute 1 is *y un minuto* (*Las diez / y un minuto.*, *La una / y un minuto.*), the
  standard reading. The bare *y uno* is colloquial and reads as broken on a display.
- 24h: *Las dieciocho / y ocho.* Hour 0 reads *Las cero / y ocho.* (*las cero horas* is the official reading).
  00:00 is the exception *Es / medianoche.* and 12:00 *Es / mediodía.*, in both modes. The other minutes of
  midnight stay *Las doce* in 12h (*las doce y ocho* is what people say; *de la noche* would need a third line).

### German (de)
- Line 1: *Zehn Uhr*, *Ein Uhr*. Line 2: *acht*, *eins*, *einundzwanzig*. This is the standard reading of 10:08 as
  *zehn Uhr acht*. The spoken *acht nach zehn* or *Viertel vor elf* would need the next hour, which the
  hour × minute table can't express.
- :00: line 1 drops *Uhr* and line 2 is *Uhr*, giving *Zehn / Uhr.* and *Ein / Uhr.*, which is exactly *zehn Uhr*.
  Rejected: *Zehn Uhr / genau.* (nobody says it at a glance) and *Punkt zehn Uhr* (*Punkt* goes first).
- 24h: *Achtzehn Uhr / acht.* Hour 0 reads *Null Uhr / acht.* and *Null / Uhr.*
- 12h: 0:00 is *Es ist / Mitternacht.* and 12:00 *Es ist / Mittag.* (colloquial for *zwölf Uhr mittags*). The
  rest of both hours reads *Zwölf Uhr / acht.* 24h keeps the official *Null Uhr* and *Zwölf Uhr*.

### Romanian (ro)
- Line 1: *Ora zece*. Line 2: *și opt*. :00 is *Ora zece / fix.*
- *Ora* was chosen over *Zece / și opt minute.* for two reasons. It keeps the time a sentence, like it/es.
  Adding *minute* would need *de minute* from 20 up, which would make line 2 the widest of every language.
- Hours agree with *ora* as DOOM says: *ora unu*, *ora două*, *ora douăsprezece*, *ora douăzeci și unu*,
  *ora douăzeci și două*. Minutes (*minut*, neuter): *și două*, *și douăsprezece*, *și douăzeci și unu*, with
  no *de* because *minute* is left out. Minute 1 is *și un minut* (*Ora zece / și un minut.*), as Romanians say
  it; *și unu* is the counting numeral.
- 14 is the everyday *paisprezece* (*patrusprezece*, ICU's choice, is the formal literary form; both are fine on
  a watch). 16 is the literary *șaisprezece* (ICU has *șasesprezece*). ș and ț use the comma below.
- 24h: *Ora optsprezece / și opt.* Hour 0 reads *Ora zero / și opt.* (the official reading). 00:00 is the exception
  *E / miezul nopții.*
- 12h: 0:00 is *E / miezul nopții.* too and 12:00 *E / amiază.* The rest of both hours reads *Ora douăsprezece*.

### Hindi (hi)
- Line 1: *दस बजकर*. Line 2: *आठ मिनट*. The period is the danda `।`, as in the app's Hindi strings.
- :00: line 1 drops *बजकर* and line 2 is *बजे*, giving *दस / बजे।*. 1:00 is the exception *एक / बजा।*: as a
  statement Hindi says *एक बजा है* (singular) but *दस बजे हैं*, and *एक बजे* alone means "at one o'clock". The
  `12h 1:00` key also covers 13:00 in 12h mode.
- 24h: *अठारह बजकर / आठ मिनट।*, the official 24-hour reading. Hour 0 reads *रात बारह बजकर / आठ मिनट।* and
  *रात बारह / बजे।* ("twelve at night"), because *शून्य बजकर* isn't said. 12h does the same for its midnight hour
  (with *रात साढ़े बारह / बजे।* at 0:30); noon is plain *बारह*, as in 24h.
- :30 uses the spoken forms, which are still exact: *डेढ़ / बजे।* (1:30), *ढाई / बजे।* (2:30) and *साढ़े तीन / बजे।*
  up to *साढ़े बारह / बजे।* (3:30-12:30). They apply in 12h, and in 24h for 0:30-12:30, where 0:30 is
  *रात साढ़े बारह / बजे।*. 24h 13:30-23:30 stay *तेरह बजकर / तीस मिनट।*, because *साढ़े तेरह* isn't said for clock
  time. That is 25 exceptions, plus the two 1:00 ones. *एक बजकर तीस मिनट* is correct but sounds like a railway
  announcement.
- :15 and :45 stay exact (*दस बजकर / पंद्रह मिनट।*). *सवा* (quarter past) would need about 36 more exceptions,
  and *पौने* (quarter to) uses the next hour, which the hour × minute table can't express. They aren't in the
  table.
- Google Sans Flex has **no Devanagari**. The face has to use the system font for Hindi, with `*` mapped to a bold
  weight and the rest to regular.
- Numbers come from ICU, except 15, which uses the standard spelling *पंद्रह* (ICU has *पन्द्रह*).

## Line widths

The face renders all 1,440 minutes in 12h and 24h mode in every language. The widest lines are below, in em
(advance width ÷ font size, shaped with HarfBuzz). "All City" sets every word in the City voice. "Mixed" sets
the starred words in City and the rest in Filler, as the face draws them. Hindi was measured with Noto Sans
Devanagari Bold and Regular, standing in for the watch's system font.

| lang | line 1, 12h (all City) | line 1, 24h (all City) | line 1 widest, mixed | line 2 (all City) | line 2, mixed |
|---|---|---|---|---|---|
| en | 4.38 *Twelve* | 8.49 *Twenty-three* | 8.49 | 8.86 *twenty-seven.* | 8.76 |
| it | 6.52 *Le quattro* | 8.70 *Le quattordici* | 8.20 | 12.42 *e cinquantaquattro.* | 12.07 |
| fr | 8.96 *Quatre heures* | 12.15 *Vingt et une heures* | 10.84 | 11.29 *cinquante-quatre.* | 11.19 |
| es | 6.62 *Las cuatro* | 9.16 *Las diecinueve* | 8.40 | 13.04 *y cincuenta y cuatro.* | 12.61 |
| de | 6.71 *Sieben Uhr* | 12.91 *Zweiundzwanzig Uhr* | 12.18 | 11.91 *siebenundzwanzig.* | 11.81 |
| ro | 11.42 *Ora douăsprezece* | 12.96 *Ora douăzeci și două* | 12.24 | 12.67 *și patruzeci și șapte.* | 12.19 |
| hi | 5.18 *ग्यारह बजकर* | 6.47 *रात बारह बजकर* | 6.17 | 6.87 *अड़तालीस मिनट।* | 6.66 |

The review changes (2026-10-08) didn't move any maximum. *Mezzogiorno* is 8.18 em, *e un minuto.* 5.89 em
mixed, and the widest Hindi :30 line, *रात साढ़े बारह*, is 5.43 em in Noto Sans Devanagari Bold.

The worst case is about 13 em, in es, ro and de. On a 450 px face with about 340 px of usable width, that means
26 px or less for one fixed size. English never goes past 9 em. Per-language sizes, or `isAutoSize` on each line,
will look better than one size for every language.

## Native review (2026-10-08)

A linguist read every table and spot-checked about 65 readings per language. They found no grammar, spelling or
agreement errors. These naturalness fixes were applied:

- **it, es, ro**: minute 1 is *e un minuto*, *y un minuto* and *și un minut* (was *e uno*, *y uno*, *și unu*).
- **it**: 24h hour 12 is *Mezzogiorno* (was *Le dodici*), matching *Mezzanotte* at hour 0.
- **hi**: *एक / बजा।* at 1:00, and *डेढ़*, *ढाई* and *साढ़े N* at :30.
- **ro**: this file now calls *paisprezece* the everyday form rather than "the DOOM spelling". The data is unchanged.

The review kept these as they are: en *Midnight / oh eight.*, *Eighteen / hundred.*, *It's / noon.* and *It's /
midnight.*; fr *pile*, *Midi*, *Minuit* and *Dix heures / une.*; de *Zehn / Uhr.* and
*Ein Uhr / eins.*; the midnight hours *Las cero*, *Ora zero*, *Null Uhr* and *रात बारह*; es *Es / medianoche.*
and ro *E / miezul nopții.*; the Hindi danda and the spellings *चौवालीस*, *तिरेपन*, *पंद्रह*.

Not added, because the current readings are correct and exceptions stay rare: de 24h 0:00 *Es ist /
Mitternacht.* and es 24h 12:00 *Es / mediodía.*

Nothing has been checked on a watch yet.
