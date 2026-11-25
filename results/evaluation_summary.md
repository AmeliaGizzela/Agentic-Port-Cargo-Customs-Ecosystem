# Hasil Evaluasi Strategi (dihasilkan otomatis)

Nilai = kondisi akhir setelah semua gangguan pada skenario. J lebih kecil = lebih baik.

## S0 - Kondisi normal

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.5752 | 300.17 | 0.5 | 4.75 | 1.0 | 0.0 | 0 | 0 | 0 |
| local | 0.8936 | 407.73 | 1.0 | 9.11 | 0.87 | 75.65 | 0 | 0 | 0 |
| collaborative | 0.5752 | 300.17 | 0.5 | 4.75 | 1.0 | 0.0 | 0 | 0 | 0 |

## S1 - Cuaca buruk: V001 terlambat ±4 jam

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.6871 | 346.55 | 1.17 | 5.58 | 1.0 | 0.0 | 2 | 0 | 0 |
| local | 0.8348 | 378.19 | 0.58 | 9.01 | 0.891 | 44.18 | 1 | 0 | 0 |
| collaborative | 0.6314 | 314.56 | 0.42 | 5.94 | 1.0 | 0.0 | 0 | 1 | 0 |

## S2 - Dokumen susulan V002 (SHP-003) lengkap

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.4752 | 300.17 | 0.5 | 4.75 | 1.0 | 0.0 | 0 | 0 | 0 |
| local | 0.7173 | 371.6 | 1.0 | 8.46 | 0.935 | 42.75 | 0 | 0 | 0 |
| collaborative | 0.4752 | 300.17 | 0.5 | 4.75 | 1.0 | 0.0 | 0 | 0 | 0 |

## S3 - Crane B1 rusak (produktivitas 34%)

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.8482 | 496.15 | 1.75 | 3.85 | 1.0 | 0.0 | 2 | 0 | 0 |
| local | 1.001 | 523.88 | 1.42 | 7.46 | 0.935 | 44.7 | 2 | 0 | 0 |
| collaborative | 0.7059 | 402.95 | 1.17 | 3.89 | 1.0 | 0.0 | 1 | 1 | 0 |

## S4 - Banjir tol Bekasi-Cikarang

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.7515 | 342.15 | 0.5 | 8.23 | 0.957 | 25.3 | 0 | 0 | 0 |
| local | 0.8831 | 388.3 | 1.0 | 9.44 | 0.826 | 51.68 | 0 | 0 | 0 |
| collaborative | 0.6981 | 315.04 | 0.5 | 7.84 | 1.0 | 0.0 | 0 | 0 | 0 |

## S5 - 4 truk ditarik 09:00-19:00

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.9755 | 470.55 | 0.5 | 9.42 | 0.826 | 149.2 | 0 | 0 | 0 |
| local | 0.9152 | 415.01 | 1.0 | 9.53 | 0.891 | 80.25 | 0 | 0 | 0 |
| collaborative | 0.6961 | 314.82 | 0.5 | 7.79 | 1.0 | 0.0 | 0 | 0 | 0 |

## S6 - Nota intelijen Bea Cukai (HS 8542 China)

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 1.008 | 587.83 | 0.5 | 5.9 | 0.891 | 281.5 | 0 | 0 | 0 |
| local | 0.8751 | 380.43 | 1.0 | 9.71 | 0.891 | 44.5 | 0 | 0 | 0 |
| collaborative | 0.6488 | 308.85 | 0.5 | 6.61 | 1.0 | 0.0 | 0 | 0 | 0 |

## S7 - Hari kacau (cuaca + dokumen + crane + banjir)

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 1.4862 | 875.55 | 2.25 | 9.27 | 0.652 | 275.07 | 2 | 0 | 0 |
| local | 1.6009 | 1023.96 | 1.5 | 7.72 | 0.717 | 504.3 | 2 | 0 | 0 |
| collaborative | 1.0414 | 583.38 | 1.08 | 8.85 | 0.674 | 85.37 | 1 | 1 | 0 |

## S8 - V001 terlambat + negosiasi alot -> mediasi Control Center

| Strategi | J_global_objective | C_total_cost_jt | avg_vessel_wait_h | avg_container_dwell_h | Q_on_time_rate | importer_penalty_jt | late_departures | negotiations | compliance_violations |
|---|---|---|---|---|---|---|---|---|---|
| static | 0.6871 | 346.55 | 1.17 | 5.58 | 1.0 | 0.0 | 2 | 0 | 0 |
| local | 0.8348 | 378.19 | 0.58 | 9.01 | 0.891 | 44.18 | 1 | 0 | 0 |
| collaborative | 0.6314 | 314.56 | 0.42 | 5.94 | 1.0 | 0.0 | 0 | 1 | 0 |

## Keterangan strategi

- **static**: Tanpa adaptasi: rencana awal dibekukan, gangguan ditangani FCFS (baseline non-agentic)
- **local**: Local enterprise intelligence: tiap perusahaan re-optimasi untuk dirinya sendiri, tanpa negosiasi/berbagi prioritas
- **collaborative**: Global ecosystem intelligence: objektif ekosistem, negosiasi A2A, mediasi, berbagi prioritas