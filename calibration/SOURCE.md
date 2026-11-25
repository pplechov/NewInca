# Калибровочные данные

Эта папка **не является частью программы**. Здесь лежит то, что относится к
конкретному прибору и конкретной калибровке; заменив содержимое, можно считать
другой прибор без пересборки `newINCA.exe`.

```
calibration\
├─ mac\                                  наборы коэффициентов поглощения
│   ├─ chantler.csv                      μ/ρ, Chantler/NIST (xraylib)
│   └─ chantler.ini                      [mac] title / source / notes
├─ mac.csv                               прежнее место одной таблицы (fallback)
├─ elements.csv, lines.csv               физика, общая для всех приборов
├─ xpp_standards.csv                     калибровка по умолчанию
├─ profile.ini                           что это за калибровка
├─ SOURCE.md
└─ profiles\
    ├─ msu-cpx-opx-amp-bt\              по папке на протокол измерения
    │   ├─ xpp_standards.csv
    │   └─ profile.ini
    ├─ msu-fsp-glass\
    ├─ msu-grt\
    ├─ msu-ol\
    └─ msu-ore\
```

Профиль — это папка с `xpp_standards.csv` и `profile.ini`. Физику
(`elements.csv`, `lines.csv`) можно положить и в саму папку профиля — тогда она
берётся оттуда, иначе из корня калибровки. Таблица μ/ρ выбирается отдельно
списком «μ/ρ» в окне (или `--mac` в командной строке) и живёт в `mac\`.

## profile.ini

```ini
[profile]
title = МГУ: оливины (Ol)
instrument = МГУ, S-3400N + X-Max 7
family = xstream
detector_serial = 000000824
detector_model = X-Max 7
e0 = 20
default = 0
source = MSU inca\Data\Calib\20kV Ol 2016 февраль\XPP Standards.mdb
notes = оливины, фаялит; строк: 269
```

* `detector_serial` — серийный номер ED-детектора, который INCA пишет в
  `XrayDetector/ED_Detector` каждого спектра. Именно по нему программа
  подбирает калибровку к открытому проекту (`newINCA.exe info` показывает выбор
  и его причину).
* `default = 1` — какой из профилей этого прибора брать, если подходят
  несколько (у МГУ это `msu-cpx-opx-amp-bt`).
* Если серийного номера нет, используется `detector_model`, а при полном
  отсутствии сведений о детекторе — профиль по умолчанию.

Выбор пользователя запоминается в `settings.ini` рядом с программой — отдельно
для каждого серийного номера.

## xpp_standards.csv — по умолчанию

Экспорт таблицы `Standards data` из
`Inca FMM\Atali\Data_brown\LP\XPP Standards.mdb`
(278 строк, измерения до февраля 2017). Файл оставлен таким, каким был в первой
версии программы.

## xpp_standards.csv — профили МГУ

Пять протоколов измерения стандартов 2016 г. из
`MSU inca\Data\Calib\<протокол>\XPP Standards.mdb` (по 269 строк):

| профиль | протокол |
|---|---|
| `msu-cpx-opx-amp-bt` | 20kV Cpx-Opx-Amp-Bt 2016 февраль |
| `msu-fsp-glass` | 20kV Fsp-glass 2016 февраль |
| `msu-grt` | 20kV Grt 2016 февраль |
| `msu-ol` | 20kV Ol 2016 февраль |
| `msu-ore` | 20kV Ore-minerals 2016 февраль |

Эти базы — Microsoft Access 97 (Jet 3). Их не открывает ни современный драйвер
ACE (64 бита), ни провайдер Jet 4.0 в 64-битном процессе: файл читается только
32-битным Jet 4.0. Поэтому экспорт делает `tools\mdb_export.cmd`:

```bat
tools\mdb_export.cmd "MSU inca\Data\Calib\20kV Ol 2016 февраль\XPP Standards.mdb" ^
                     "calibration\profiles\msu-ol\xpp_standards.csv"
```

Скрипт (`tools\mdb_export.ps1`) копирует базу во временную папку (Jet создаёт
рядом `.ldb`), читает таблицу `Standards data` и пишет CSV с теми же 16
столбцами, что и калибровка по умолчанию. Ничего в папке INCA не меняется.

Столбцы:

| столбец | смысл |
|---|---|
| `Atomic Number` | Z элемента |
| `Line Type` | 1000/1001/1002 = K/L/M |
| `Detector Type` | 1/2 — номер ED-детектора в системе |
| `Norm Area Std` | измеренная интенсивность стандарта |
| `Conc Std` | содержание элемента в стандарте, мас.% |
| `E0` | ускоряющее напряжение, кВ |
| `Elevation` | угол отбора, градусы (40) |
| `Efficiency ratio` | относительная эффективность детектора |
| `Label` | название стандарта (SiO2, Di N1, Ol-111312 …) |
| `Date` | дата измерения |

Именно по этой таблице программа считает k-отношения.

## mac.csv / mac\chantler.csv

Коэффициенты массового поглощения μ/ρ, см²/г, для Z = 1…92 и набора энергий
линий. Источник — библиотека **xraylib** (данные Chantler/NIST), выгружено
скриптом `make_physics.py`. Столбцы: `Z,energy_keV,mu_over_rho_cm2_per_g`.

## Наборы μ/ρ (mac\)

Таблица поглощения — независимая физика, поэтому она вынесена в отдельные файлы
и выбирается в интерфейсе (список «μ/ρ») или ключом `--mac`:

| файл | что это |
|---|---|
| `mac\chantler.csv` | Chantler/NIST из xraylib, по умолчанию |
| `mac\<имя>.csv` | любой свой набор в том же формате |
| `mac\<имя>.ini` | `[mac] title = …`, `source = …`, `notes = …` |

Свой файл достаточно положить в папку — пересборка не нужна. Профиль может
указать набор строкой `mac = <имя>` в `profile.ini`; выбор пользователя
запоминается в `settings.ini` и имеет приоритет.

### Почему здесь нет таблицы INCA

В INCA μ/ρ не лежит файлом. Её считает
`OIMAGCorrectionProcedures.dll` (`IQuantFunctions.CalcMassAbsorpCoeffByEnergy`)
по лицензируемой «X-ray database» (`IXrayDatabase` в
`OIMAGElementProperties.dll`, диапазон Z = 1…98). Проверено на поставке
INCA 18 D SP4: ни одного файла с таблицей μ/ρ в ней нет, только базы
стандартов Access (`XPP Standards.mdb`, `Factory.mdb`, `wavestds.mdb`) и код.

Сам COM-объект загружается без установки INCA (через `DllGetClassObject`,
даже без прав администратора), `Initialise()` отвечает успехом, но на любой
запрос коэффициента возвращается `0x80040513 «The password for the X-ray
database has not been set»`: нужен вендорский ключ Oxford. Перебор пустой
строки, `x` и `INCA` не проходит; в пакете ключа нет. Подробный протокол
попытки — `_analysis\mac_inca\README.md`.

Практический вывод: чтобы получить «как в INCA», таблицу надо выгрузить там,
где INCA установлена и лицензирована (в её UI или тем же скриптом
`_analysis\mac_inca\dump_mac.ps1 -Password <ключ>`), и положить CSV в
`calibration\mac\`.

## elements.csv

`Z;symbol;atomic_weight;fluor_yield_K;fluor_yield_L3;jump_K` — из той же
библиотеки.

## lines.csv

`Z;symbol;line;energy_keV` для Kα1, Kβ1, Lα1, Lβ1, Mα1.

## Если калибровки нет

Программа откроет проект и сохранит изображения; таблица концентраций не
построится, о чём будет сообщение в журнале. Чтобы добавить свой прибор,
достаточно создать `profiles\<имя>\` с `xpp_standards.csv` и `profile.ini` —
пересборка не нужна.
