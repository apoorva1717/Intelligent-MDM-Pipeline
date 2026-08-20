"""ISO 20275 Entity Legal Form (ELF) codes, split by commercial character.

GENERATED from the GLEIF ELF registry (``api.gleif.org/api/v1/entity-legal-forms``,
1073 of 3,599 active forms classified; the rest carry no clear signal
and are deliberately absent — an unlisted code means "no evidence", not "company").

A GLEIF ``lei-records`` response gives only ``entity.legalForm.id`` — the code,
not its name — so the name→character decision is made here, once, at development
time. Nothing looks a code up at runtime: this is a lookup table over fields the
pipeline already fetches, not a new service dependency.

Precision over recall, in both directions:

* A form is **non-commercial** only when its name says so outright — "Non-Profit
  Corporation", "Nonstock Corporation", "eingetragener Verein", "Stiftung des
  öffentlichen Rechts", a bare "Foundation". Names that merely sound charitable
  while describing a trading entity are excluded by an explicit veto list
  ("For-Profit Public Benefit Corporation", "Savings and Loan Association",
  "Business Trust", "Trust Company", every cooperative and credit union).
* A form is **commercial** only on an unambiguous trading form (Corporation,
  Aktiengesellschaft, Limited, Partnership, …), and never when the name also
  carries a non-profit marker — "Nonstock Corporation" and "Corporation
  (Nonprofit)" both contain "Corporation" and must not be read as commercial.

Verified against live GLEIF records: Yale (7W53 Nonstock Corporation), Mayo
(9I4Y Non-Profit Corporation), Cleveland Clinic (7VK5 Corporation (Nonprofit))
and Max-Planck (QZ3L eingetragener Verein) land non-commercial; Pfizer and
Bruker (XTIQ Corporation), Lockheed (HLR4 Stock Corporation), Siemens (6QQB
Aktiengesellschaft), BASF (SGST Europäische Aktiengesellschaft) and Novartis
(MVII Company limited by shares) land commercial; Harvard University Employees
Credit Union (14OD) falls through to the next evidence source, which is right —
a credit union is neither.

Note the codes that carry no name at all: **8888** ("other") and **9999** ("not
on the list") are catch-alls, and both MIT (8888, other="INSTITUTE") and Pfizer
Canada (8888, other="INCORPORATED / INCORPOREE") use 8888. Neither appears in
either set; the free text in ``legalForm.other`` is matched separately.
"""

from __future__ import annotations

# Forms whose name states a non-commercial character outright.
NON_COMMERCIAL_ELF: frozenset[str] = frozenset({
    "11GD", "12N6", "1K9U", "1ONH", "29OB", "2JZ4", "2XXG", "358I",
    "35QN", "375P", "3N55", "3OVJ", "3U3Y", "3YUS", "40A7", "47LQ",
    "520I", "639V", "6EDK", "76ME", "78H5", "79H0", "7BQ5", "7T8N",
    "7VK5", "7W53", "9EJ6", "9I4Y", "9XFK", "AVLE", "BADE", "C1F4",
    "CB98", "CVXK", "DAVB", "DMNZ", "DX2N", "E9OX", "EF4Y", "EVBW",
    "EVE6", "FEBD", "G1P6", "HSPI", "HUSW", "IY8C", "JF6D", "JHC4",
    "JWWT", "K2BJ", "K7YU", "LU9O", "ME4Y", "N00R", "NOBH", "O256",
    "O4NK", "OGSS", "OJBU", "P3LZ", "PDLV", "PKZ2", "PLMC", "PNZI",
    "PUJR", "PXGA", "QZ3L", "R7JP", "R7QO", "RD1T", "RHVP", "RR8G",
    "S7VR", "SCE1", "SCX8", "SJKV", "SQKS", "STX7", "T4M6", "U87X",
    "UZ83", "V65U", "V89I", "VKZD", "VVOW", "W0U4", "WMJ9", "XHWP",
    "XLWA", "Y80K", "YA01", "YJ4C", "ZJPG", "ZUHK", "ZVS9",
})

# Unambiguous trading forms.
COMMERCIAL_ELF: frozenset[str] = frozenset({
    "0NYS", "15BP", "15JS", "17FJ", "17R0", "1A0A", "1ADA", "1ASH",
    "1CZS", "1E9M", "1G26", "1GR6", "1HXP", "1JGX", "1PWF", "1Q2B",
    "1QMT", "1S9L", "1TG4", "1TX8", "1V0I", "1W6I", "1WZP", "1XME",
    "21F4", "21OE", "23Q4", "23V9", "23XJ", "254M", "2AUN", "2B81",
    "2CC3", "2DGO", "2F0A", "2FHC", "2HBR", "2I4P", "2LNV", "2RVP",
    "2SPI", "2Y95", "30PQ", "30TX", "33IP", "35RF", "36AO", "36KV",
    "36YD", "3DXJ", "3EKS", "3EPR", "3FP6", "3JTE", "3MM4", "3MMY",
    "3P03", "3Q15", "3QSG", "3RHN", "3W7E", "3Y5C", "3ZBR", "3ZXC",
    "40SO", "42GN", "43LY", "44LR", "456O", "46QC", "487P", "4A3J",
    "4C0J", "4EWZ", "4GJI", "4HTX", "4IXL", "4JCS", "4LEA", "4M62",
    "4TMV", "4U9V", "4WN7", "4XMS", "4XP8", "4YOA", "4YUU", "52U0",
    "530K", "53DC", "54M6", "54WI", "56MU", "56NA", "58N0", "5AX8",
    "5D9I", "5DS0", "5E0K", "5EEA", "5EKE", "5FRT", "5GGB", "5HQ4",
    "5I7B", "5JAL", "5K70", "5KVT", "5MNR", "5MRP", "5OET", "5Q66",
    "5RDO", "5RRE", "5SGE", "5SGV", "5T3W", "60MF", "62L3", "64HG",
    "672E", "6E4G", "6EH6", "6GEC", "6HT0", "6I75", "6ICB", "6IIM",
    "6JCE", "6M6O", "6M6Z", "6MB6", "6MS4", "6QQB", "6S32", "6TGS",
    "6TPA", "6TPE", "6TRA", "6U2Q", "6UED", "6XB7", "6ZAR", "70EO",
    "70NZ", "71ZI", "72CA", "74LJ", "75XK", "771I", "784B", "78PB",
    "78QF", "7AJT", "7AS7", "7AWY", "7CDL", "7F5B", "7FCS", "7GTR",
    "7H0X", "7HY7", "7IKZ", "7IYW", "7K6U", "7MGJ", "7OS8", "7OYN",
    "7P8Y", "7PC4", "7QEH", "7QV2", "7QWA", "7RLC", "7RRP", "7RZH",
    "7TJ1", "7U8S", "7VKC", "7WL9", "7XPF", "7XUS", "7Z3M", "7ZU4",
    "81WV", "84DD", "85EC", "88OX", "8APW", "8CF0", "8HNT", "8HR7",
    "8KA4", "8KL5", "8MBD", "8MEC", "8MY7", "8N21", "8OZ0", "8PIA",
    "8PUO", "8QMN", "8RLE", "8WM4", "8XN0", "8YBQ", "8Z6G", "8ZH8",
    "8ZUV", "94UQ", "95OK", "98A8", "9A4Q", "9AAS", "9B78", "9BQH",
    "9C19", "9D1K", "9FD9", "9FPZ", "9GXA", "9HKD", "9I58", "9L6B",
    "9M2Q", "9OSW", "9STD", "9TCY", "9U6F", "A0PS", "A30N", "A770",
    "A7G6", "A7VA", "A9AW", "A9N4", "A9VQ", "ABII", "AD0H", "AEK0",
    "AHRM", "AIR0", "AJ2Y", "AJ9U", "ALW3", "AMKW", "AN8Z", "ANSR",
    "AO0O", "AOS4", "AQBO", "ARON", "ASC7", "ASR5", "ASWX", "AXSB",
    "AZ8V", "AZUK", "B13W", "B38Q", "B3RX", "B3WQ", "B5PM", "B5YE",
    "B6ES", "B843", "B8CE", "B8KO", "B8XC", "BAMJ", "BBEB", "BC32",
    "BC38", "BF9N", "BFIV", "BGH4", "BJ2V", "BO6L", "BOYK", "BQ0P",
    "BQRL", "BRO8", "BSLM", "BST2", "BTQ1", "BVJ0", "BXZH", "BYFU",
    "C061", "C276", "C58S", "C5K7", "C609", "C9KZ", "CAF9", "CAGH",
    "CD9O", "CDOV", "CDXK", "CDYY", "CHWX", "CHX6", "CNQ3", "COZR",
    "CQMY", "CR3H", "CR7N", "CSUT", "CUQF", "CW38", "CWRI", "CWTQ",
    "CXIO", "D155", "D1SL", "D3UY", "D4YS", "D8PB", "D90J", "D92T",
    "DAEG", "DBGD", "DBU3", "DDKQ", "DE7H", "DEMP", "DFPP", "DHGI",
    "DHY2", "DK7L", "DNF1", "DQAU", "DQBZ", "DQMJ", "DQMV", "DQUB",
    "DRSE", "DSII", "DU35", "DW2W", "DWS3", "E0NE", "E281", "E3YL",
    "E6J3", "E9CM", "EAD0", "EBP8", "EBRQ", "ECLT", "EHWD", "EI4J",
    "EIDL", "EJX1", "EKJG", "EM3H", "EMLA", "EMLK", "EPG7", "EQOV",
    "EQQP", "ER0H", "ER7C", "EULU", "EURM", "EZL8", "EZNQ", "F0A6",
    "F2QV", "F3UE", "F5VL", "F7KI", "F8DD", "F8VX", "F9J3", "FBN0",
    "FBQL", "FC7L", "FCAN", "FE1L", "FF1D", "FFBM", "FG2L", "FGT5",
    "FGVH", "FHRL", "FHZY", "FK53", "FKVG", "FMPL", "FN6X", "FPZK",
    "FRUF", "FW66", "G0HE", "G3JV", "G4YY", "G66U", "G6M8", "G6VI",
    "G73Q", "G7H0", "G9PJ", "GCCT", "GCU5", "GEE0", "GG0S", "GHB2",
    "GL33", "GLCI", "GNNT", "GOGQ", "GORM", "GQ8F", "GQVQ", "GTBR",
    "GU5E", "GW1T", "GXBE", "GZMZ", "H0PO", "H1UM", "H35I", "H64R",
    "H725", "H8MU", "H987", "HCBE", "HFGV", "HK03", "HKWO", "HLCG",
    "HLR4", "HN8W", "HNJK", "HNPH", "HNSU", "HOV4", "HP0B", "HPKC",
    "HQJV", "HQKE", "HQYG", "HRKG", "HSEV", "HTBY", "HV3B", "HW77",
    "HX77", "HZEH", "I2XB", "I3Z9", "I47Z", "I5XP", "I8G4", "I93O",
    "I9K6", "ICXT", "ID30", "IDFN", "IIZ4", "IJAO", "IJHI", "IKGM",
    "IMDT", "IN4H", "IODM", "IOFN", "IPGV", "IVQQ", "IWNQ", "IWZF",
    "IYZI", "J1V0", "J3C5", "J3T2", "J4JC", "J5RC", "J76S", "J98R",
    "JBBW", "JBQI", "JDX6", "JFET", "JGNU", "JGV1", "JH78", "JIF7",
    "JJ9M", "JKOT", "JNAD", "JOX1", "JOZN", "JS65", "JTJE", "JUDZ",
    "JXDX", "JZED", "JZWN", "K4MF", "K4OX", "K575", "K6G7", "KAEM",
    "KC7Z", "KFPS", "KGUS", "KGZ8", "KJ0B", "KJ1Y", "KJOW", "KMFX",
    "KNIC", "KOFC", "KORB", "KPH8", "KU3O", "KVC1", "KYOI", "L05H",
    "L10T", "L1PM", "L22N", "L25Z", "L2DM", "L2QV", "L5DU", "L7HH",
    "L9JC", "LBJ1", "LBPW", "LC2L", "LCVA", "LF4E", "LGWG", "LH0Q",
    "LJH5", "LJYQ", "LKD5", "LKQ2", "LMSY", "LNBY", "LO9A", "LOJL",
    "LOL8", "LPLY", "LQZC", "LRRN", "LT1Y", "LV28", "LVC6", "LVRX",
    "LWV6", "LZFR", "LZI3", "LZIC", "M1FY", "M27U", "M2KR", "M44Q",
    "M44Y", "M4FO", "M5RM", "M64D", "M6YY", "M848", "M886", "M9A1",
    "MBVS", "MCQP", "MEHL", "MFYJ", "MGUM", "MH3L", "MIPY", "MJJZ",
    "MM8M", "MNQ7", "MP7S", "MPFG", "MPUG", "MQH3", "MRSY", "MT4X",
    "MTDW", "MTLX", "MVII", "MY93", "MZB1", "N0JF", "N0YQ", "N10D",
    "N124", "N28C", "N3VO", "N5NT", "N69M", "N745", "N97C", "N9QH",
    "NAQG", "NB58", "NBCI", "NBTW", "NDBR", "NDC3", "NETO", "NFPC",
    "NGR5", "NGZJ", "NHBK", "NHGN", "NHYA", "NI44", "NK3V", "NNLM",
    "NO8C", "NYUD", "O13B", "O15R", "O4QG", "O5PH", "O6QP", "O85W",
    "O90R", "OA6N", "OCVJ", "OE6T", "OESH", "OF3Q", "OGHV", "OJ9K",
    "OJDX", "ONF1", "ONJ1", "OO14", "OOX5", "OPRT", "OSBR", "OSE2",
    "OT3Y", "OTE3", "OTU7", "OVBT", "OWR6", "OXAZ", "OZ3O", "P0LR",
    "P0TZ", "P28E", "P2R9", "P3YJ", "P418", "P65N", "P7RH", "P7VS",
    "P8D7", "PB7W", "PCO0", "PFPW", "PFY0", "PH5T", "PIDT", "PJ10",
    "PKBG", "PKOR", "PNSZ", "PQHL", "PQXK", "PVT3", "PXPK", "PZR6",
    "Q0M5", "Q1N4", "Q367", "Q49K", "Q62B", "Q7WY", "Q82Q", "Q8RV",
    "Q9VK", "Q9Y1", "QEEJ", "QF4W", "QFLH", "QGSA", "QJ9F", "QJBA",
    "QJVN", "QK1F", "QK2J", "QLWR", "QM7Z", "QMI2", "QN8Y", "QNSC",
    "QODJ", "QOO6", "QP3O", "QR25", "QR4Y", "QSHG", "QUJ5", "QVPB",
    "QWFD", "QWPR", "QX9N", "QZB4", "QZMH", "QZTT", "R0BI", "R0KX",
    "R155", "R18M", "R27J", "R2L8", "R2PI", "R2YL", "R4KK", "R5UT",
    "R85P", "R8PY", "R997", "RBBY", "RC5L", "RCE0", "RCGI", "RCNI",
    "REVF", "RG0W", "RH6N", "RIYP", "RKLI", "RKYF", "RLB2", "RN4K",
    "RQDD", "RR3L", "RRXD", "RSWJ", "RTVE", "RU6X", "RWVW", "RWX4",
    "RY5B", "RZ5R", "S02Y", "S0BB", "S2E3", "S2K8", "S3K4", "S69C",
    "S745", "S779", "S7R4", "S8DM", "SBF3", "SDX0", "SGST", "SMZ6",
    "SOX5", "SP51", "SP9F", "SPX9", "SQ7B", "SQ8U", "SQXV", "SS1V",
    "STBC", "SUMS", "SXX9", "SXY3", "SYPT", "T0CV", "T0XH", "T0YJ",
    "T172", "T2JS", "T362", "T5UM", "T5ZE", "T69Z", "T7D4", "T80N",
    "T91C", "T91T", "TA98", "TA9Z", "TCVB", "TDBW", "TE79", "TEGV",
    "TEPY", "TGLV", "TGMR", "TJ6V", "TKPE", "TL87", "TPFL", "TPJZ",
    "TPTU", "TPZ2", "TQ3Z", "TRI2", "TRS2", "TT2H", "TTB3", "TTIF",
    "TTTV", "TXCW", "TXVC", "TY0S", "TYW0", "U2PN", "U5AR", "U5RF",
    "U5S6", "U77P", "U7GR", "U7HC", "U89P", "U938", "U94O", "U9HL",
    "UAES", "UBV0", "UBWU", "UDG6", "UDLA", "UE3G", "UF6Y", "UJZB",
    "UK9P", "ULJ1", "UQWQ", "URQH", "USJP", "UUYB", "UW1C", "UWMY",
    "UX5E", "UY66", "UZ9W", "UZUP", "V10Q", "V2PA", "V5G5", "V7FT",
    "V89C", "VA1X", "VAPN", "VATV", "VBJW", "VC3E", "VE28", "VFXJ",
    "VG3S", "VJRL", "VKPN", "VKSV", "VNIU", "VPBH", "VPRH", "VQV6",
    "VSDJ", "VTIP", "VUXH", "VV0W", "VVOX", "VVPD", "VXDE", "VYAX",
    "W3YV", "W4DB", "W6A7", "W6NI", "W9ZD", "WBKH", "WDT2", "WE9D",
    "WEIB", "WF3Q", "WFYL", "WHGH", "WIU6", "WKFR", "WNKG", "WNV6",
    "WP5I", "WPCN", "WQLU", "WR09", "WRF9", "WTWK", "WUAZ", "WYG5",
    "X0MP", "X0SD", "X1EL", "X2X1", "X32V", "X4EB", "X4IT", "XAQA",
    "XBK3", "XE4Z", "XEOV", "XH5R", "XHCV", "XIZI", "XJOT", "XLEO",
    "XOAD", "XPE5", "XQEC", "XSNP", "XST3", "XSZY", "XTIQ", "XTZG",
    "XVC6", "XXCZ", "Y182", "Y21X", "Y2L3", "Y33B", "Y8CL", "Y8LH",
    "Y8W9", "YB1Q", "YG5M", "YIIS", "YJZ3", "YN9Y", "YOB3", "YOIW",
    "YOP9", "YPG1", "YQDQ", "YQLO", "YRMK", "YS16", "YSBJ", "YSP9",
    "YTY3", "YVSB", "YVSL", "YVZD", "YWK0", "YY5A", "Z0EY", "Z3P8",
    "Z3Y2", "Z4YC", "Z4ZP", "Z54A", "Z6ZU", "Z7ER", "Z92A", "Z9C5",
    "Z9CH", "ZACY", "ZCFU", "ZD39", "ZE1G", "ZEZ2", "ZGPY", "ZHED",
    "ZHT0", "ZJEX", "ZJTK", "ZJZB", "ZLTD", "ZQ6S", "ZQEN", "ZSFX",
    "ZULC", "ZWYK",
})
