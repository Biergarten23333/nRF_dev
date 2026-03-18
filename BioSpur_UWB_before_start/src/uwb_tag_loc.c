#include "uwb_tag_loc.h"

#include "uwb_anchor_layout.h"
#include "uwb_ss_twr_shared.h"

#include <math.h>
#include <string.h>

#define UWB_TAG_LOC_MIN_ANCHORS 4U
#define UWB_TAG_LOC_MIN_QUALITY_PERCENT 50U
#define UWB_TAG_LOC_MAX_ITERATIONS 8U
#define UWB_TAG_LOC_MAX_CANDIDATES UWB_MAX_ANCHORS
#define UWB_TAG_LOC_SIZE_PENALTY_MM 40.0
#define UWB_TAG_LOC_MAX_RES_WEIGHT 0.35
#define UWB_TAG_LOC_ONE_PLANE_PENALTY_MM 400.0
#define UWB_TAG_LOC_XY_MARGIN_M 0.75
#define UWB_TAG_LOC_Z_MARGIN_M 0.30
#define UWB_TAG_LOC_VOLUME_PENALTY_WEIGHT 3.0

struct uwb_tag_loc_vector {
    double x;
    double y;
    double z;
};

struct uwb_tag_loc_candidate {
    uint8_t anchor_id;
    uint8_t quality_percent;
    bool lower_plane;
    bool upper_plane;
    struct uwb_tag_loc_vector pos_m;
    double range_m;
};

struct uwb_tag_loc_bounds {
    double min_x;
    double max_x;
    double min_y;
    double max_y;
    double min_z;
    double max_z;
};

static bool uwb_tag_loc_solve_3x3(double a[3][3], double b[3], double out[3])
{
    int i;

    for (i = 0; i < 3; ++i) {
        int pivot = i;
        double max_abs = fabs(a[i][i]);

        for (int row = i + 1; row < 3; ++row) {
            double candidate = fabs(a[row][i]);
            if (candidate > max_abs) {
                max_abs = candidate;
                pivot = row;
            }
        }

        if (max_abs < 1e-9) {
            return false;
        }

        if (pivot != i) {
            for (int col = i; col < 3; ++col) {
                double tmp = a[i][col];
                a[i][col] = a[pivot][col];
                a[pivot][col] = tmp;
            }
            {
                double tmp = b[i];
                b[i] = b[pivot];
                b[pivot] = tmp;
            }
        }

        for (int row = i + 1; row < 3; ++row) {
            double factor = a[row][i] / a[i][i];
            for (int col = i; col < 3; ++col) {
                a[row][col] -= factor * a[i][col];
            }
            b[row] -= factor * b[i];
        }
    }

    for (i = 2; i >= 0; --i) {
        double sum = b[i];
        for (int col = i + 1; col < 3; ++col) {
            sum -= a[i][col] * out[col];
        }
        out[i] = sum / a[i][i];
    }

    return true;
}

static uint8_t uwb_tag_loc_popcount_u32(uint32_t mask)
{
    uint8_t count = 0U;

    while (mask != 0U) {
        count += (uint8_t)(mask & 1U);
        mask >>= 1;
    }

    return count;
}

static bool uwb_tag_loc_linear_seed(
    const struct uwb_tag_loc_candidate *candidates,
    size_t candidate_count,
    uint32_t subset_mask,
    struct uwb_tag_loc_vector *seed)
{
    size_t ref_index = SIZE_MAX;
    double ref_norm_sq = 0.0;
    double ref_range_sq = 0.0;
    double ata[3][3] = {{0.0}};
    double atb[3] = {0.0, 0.0, 0.0};
    double solution[3] = {0.0, 0.0, 0.0};

    for (size_t i = 0; i < candidate_count; ++i) {
        if ((subset_mask & (1UL << i)) != 0U) {
            ref_index = i;
            break;
        }
    }

    if (ref_index == SIZE_MAX) {
        return false;
    }

    ref_norm_sq = candidates[ref_index].pos_m.x * candidates[ref_index].pos_m.x +
                  candidates[ref_index].pos_m.y * candidates[ref_index].pos_m.y +
                  candidates[ref_index].pos_m.z * candidates[ref_index].pos_m.z;
    ref_range_sq = candidates[ref_index].range_m * candidates[ref_index].range_m;

    for (size_t i = 0; i < candidate_count; ++i) {
        double row[3];
        double norm_sq;
        double rhs;

        if (i == ref_index || (subset_mask & (1UL << i)) == 0U) {
            continue;
        }

        row[0] = 2.0 * (candidates[i].pos_m.x - candidates[ref_index].pos_m.x);
        row[1] = 2.0 * (candidates[i].pos_m.y - candidates[ref_index].pos_m.y);
        row[2] = 2.0 * (candidates[i].pos_m.z - candidates[ref_index].pos_m.z);

        norm_sq = candidates[i].pos_m.x * candidates[i].pos_m.x +
                  candidates[i].pos_m.y * candidates[i].pos_m.y +
                  candidates[i].pos_m.z * candidates[i].pos_m.z;

        rhs = ref_range_sq - candidates[i].range_m * candidates[i].range_m -
              ref_norm_sq + norm_sq;

        for (int r = 0; r < 3; ++r) {
            atb[r] += row[r] * rhs;
            for (int c = 0; c < 3; ++c) {
                ata[r][c] += row[r] * row[c];
            }
        }
    }

    if (!uwb_tag_loc_solve_3x3(ata, atb, solution)) {
        return false;
    }

    seed->x = solution[0];
    seed->y = solution[1];
    seed->z = solution[2];
    return true;
}

static bool uwb_tag_loc_refine_gauss_newton(
    const struct uwb_tag_loc_candidate *candidates,
    size_t candidate_count,
    uint32_t subset_mask,
    struct uwb_tag_loc_vector *estimate)
{
    for (uint8_t iter = 0U; iter < UWB_TAG_LOC_MAX_ITERATIONS; ++iter) {
        double h[3][3] = {{0.0}};
        double g[3] = {0.0, 0.0, 0.0};
        double delta[3] = {0.0, 0.0, 0.0};

        for (size_t i = 0; i < candidate_count; ++i) {
            double dx;
            double dy;
            double dz;
            double predicted;
            double residual;
            double jacobian[3];
            double weight;

            if ((subset_mask & (1UL << i)) == 0U) {
                continue;
            }

            dx = estimate->x - candidates[i].pos_m.x;
            dy = estimate->y - candidates[i].pos_m.y;
            dz = estimate->z - candidates[i].pos_m.z;
            predicted = sqrt(dx * dx + dy * dy + dz * dz);
            if (predicted < 1e-6) {
                predicted = 1e-6;
            }

            residual = predicted - candidates[i].range_m;
            jacobian[0] = dx / predicted;
            jacobian[1] = dy / predicted;
            jacobian[2] = dz / predicted;
            weight = 0.25 + ((double)candidates[i].quality_percent / 100.0);

            for (int r = 0; r < 3; ++r) {
                g[r] += weight * jacobian[r] * residual;
                for (int c = 0; c < 3; ++c) {
                    h[r][c] += weight * jacobian[r] * jacobian[c];
                }
            }
        }

        if (!uwb_tag_loc_solve_3x3(h, g, delta)) {
            return false;
        }

        estimate->x -= delta[0];
        estimate->y -= delta[1];
        estimate->z -= delta[2];

        if (fabs(delta[0]) < 1e-4 && fabs(delta[1]) < 1e-4 &&
            fabs(delta[2]) < 1e-4) {
            break;
        }
    }

    return true;
}

static void uwb_tag_loc_compute_residuals(
    const struct uwb_tag_loc_candidate *candidates,
    size_t candidate_count,
    uint32_t subset_mask,
    const struct uwb_tag_loc_vector *estimate,
    double *rms_m,
    double *max_abs_m,
    uint8_t *lower_count,
    uint8_t *upper_count)
{
    double residual_sum_sq = 0.0;
    double max_residual = 0.0;
    uint8_t used = 0U;
    uint8_t lower = 0U;
    uint8_t upper = 0U;

    for (size_t i = 0; i < candidate_count; ++i) {
        double dx;
        double dy;
        double dz;
        double predicted;
        double residual;

        if ((subset_mask & (1UL << i)) == 0U) {
            continue;
        }

        dx = estimate->x - candidates[i].pos_m.x;
        dy = estimate->y - candidates[i].pos_m.y;
        dz = estimate->z - candidates[i].pos_m.z;
        predicted = sqrt(dx * dx + dy * dy + dz * dz);
        residual = predicted - candidates[i].range_m;
        residual_sum_sq += residual * residual;
        if (fabs(residual) > max_residual) {
            max_residual = fabs(residual);
        }

        if (candidates[i].lower_plane) {
            lower++;
        } else if (candidates[i].upper_plane) {
            upper++;
        }

        used++;
    }

    *rms_m = (used == 0U) ? 0.0 : sqrt(residual_sum_sq / (double)used);
    *max_abs_m = max_residual;
    *lower_count = lower;
    *upper_count = upper;
}

static void uwb_tag_loc_compute_bounds(
    const struct uwb_tag_loc_candidate *candidates,
    size_t candidate_count,
    struct uwb_tag_loc_bounds *bounds)
{
    bounds->min_x = candidates[0].pos_m.x;
    bounds->max_x = candidates[0].pos_m.x;
    bounds->min_y = candidates[0].pos_m.y;
    bounds->max_y = candidates[0].pos_m.y;
    bounds->min_z = candidates[0].pos_m.z;
    bounds->max_z = candidates[0].pos_m.z;

    for (size_t i = 1; i < candidate_count; ++i) {
        if (candidates[i].pos_m.x < bounds->min_x) {
            bounds->min_x = candidates[i].pos_m.x;
        }
        if (candidates[i].pos_m.x > bounds->max_x) {
            bounds->max_x = candidates[i].pos_m.x;
        }
        if (candidates[i].pos_m.y < bounds->min_y) {
            bounds->min_y = candidates[i].pos_m.y;
        }
        if (candidates[i].pos_m.y > bounds->max_y) {
            bounds->max_y = candidates[i].pos_m.y;
        }
        if (candidates[i].pos_m.z < bounds->min_z) {
            bounds->min_z = candidates[i].pos_m.z;
        }
        if (candidates[i].pos_m.z > bounds->max_z) {
            bounds->max_z = candidates[i].pos_m.z;
        }
    }
}

static double uwb_tag_loc_axis_overshoot(double value, double min_bound,
                                         double max_bound, double margin)
{
    if (value < (min_bound - margin)) {
        return (min_bound - margin) - value;
    }

    if (value > (max_bound + margin)) {
        return value - (max_bound + margin);
    }

    return 0.0;
}

int uwb_tag_loc_solve(const struct uwb_tag_measurement *measurements,
                      size_t measurement_count,
                      struct uwb_tag_location_result *result)
{
    struct uwb_tag_loc_candidate candidates[UWB_TAG_LOC_MAX_CANDIDATES];
    struct uwb_tag_loc_bounds bounds;
    size_t candidate_count = 0U;
    double best_score = 0.0;
    bool best_valid = false;
    uint32_t best_mask = 0U;
    struct uwb_tag_loc_vector best_estimate = {0.0, 0.0, 0.0};
    double best_rms_m = 0.0;
    double best_max_residual_m = 0.0;
    uint8_t best_lower_count = 0U;
    uint8_t best_upper_count = 0U;

    memset(result, 0, sizeof(*result));

    for (size_t i = 0; i < measurement_count && candidate_count < UWB_MAX_ANCHORS;
         ++i) {
        const struct uwb_anchor_pose_mm *pose;

        if (!measurements[i].valid ||
            measurements[i].quality_percent < UWB_TAG_LOC_MIN_QUALITY_PERCENT) {
            continue;
        }

        pose = uwb_anchor_layout_get(measurements[i].anchor_id);
        if (pose == NULL) {
            continue;
        }

        candidates[candidate_count].anchor_id = measurements[i].anchor_id;
        candidates[candidate_count].quality_percent =
            measurements[i].quality_percent;
        candidates[candidate_count].lower_plane =
            uwb_anchor_layout_is_lower_plane(measurements[i].anchor_id);
        candidates[candidate_count].upper_plane =
            uwb_anchor_layout_is_upper_plane(measurements[i].anchor_id);
        candidates[candidate_count].pos_m.x = (double)pose->x_mm / 1000.0;
        candidates[candidate_count].pos_m.y = (double)pose->y_mm / 1000.0;
        candidates[candidate_count].pos_m.z = (double)pose->z_mm / 1000.0;
        candidates[candidate_count].range_m =
            (double)measurements[i].range_mm / 1000.0;
        candidate_count++;
    }

    if (candidate_count < UWB_TAG_LOC_MIN_ANCHORS) {
        return -1;
    }

    uwb_tag_loc_compute_bounds(candidates, candidate_count, &bounds);

    for (uint32_t mask = 0U; mask < (1UL << candidate_count); ++mask) {
        struct uwb_tag_loc_vector estimate;
        double rms_m;
        double max_residual_m;
        double score;
        double volume_penalty_m;
        uint8_t subset_size;
        uint8_t lower_count;
        uint8_t upper_count;

        subset_size = uwb_tag_loc_popcount_u32(mask);
        if (subset_size < UWB_TAG_LOC_MIN_ANCHORS) {
            continue;
        }

        if (!uwb_tag_loc_linear_seed(candidates, candidate_count, mask,
                                     &estimate)) {
            continue;
        }

        if (!uwb_tag_loc_refine_gauss_newton(candidates, candidate_count, mask,
                                             &estimate)) {
            continue;
        }

        uwb_tag_loc_compute_residuals(candidates, candidate_count, mask,
                                      &estimate, &rms_m, &max_residual_m,
                                      &lower_count, &upper_count);

        if (lower_count == 0U || upper_count == 0U) {
            continue;
        }

        score = rms_m * 1000.0 + max_residual_m * 1000.0 *
                                     UWB_TAG_LOC_MAX_RES_WEIGHT +
                (double)(candidate_count - subset_size) *
                    UWB_TAG_LOC_SIZE_PENALTY_MM;

        volume_penalty_m =
            uwb_tag_loc_axis_overshoot(estimate.x, bounds.min_x, bounds.max_x,
                                       UWB_TAG_LOC_XY_MARGIN_M) +
            uwb_tag_loc_axis_overshoot(estimate.y, bounds.min_y, bounds.max_y,
                                       UWB_TAG_LOC_XY_MARGIN_M) +
            uwb_tag_loc_axis_overshoot(estimate.z, bounds.min_z, bounds.max_z,
                                       UWB_TAG_LOC_Z_MARGIN_M);
        score += volume_penalty_m * 1000.0 * UWB_TAG_LOC_VOLUME_PENALTY_WEIGHT;

        if (lower_count < 2U) {
            score += UWB_TAG_LOC_ONE_PLANE_PENALTY_MM;
        }
        if (upper_count < 2U) {
            score += UWB_TAG_LOC_ONE_PLANE_PENALTY_MM;
        }

        if (!best_valid || score < best_score) {
            best_valid = true;
            best_score = score;
            best_mask = mask;
            best_estimate = estimate;
            best_rms_m = rms_m;
            best_max_residual_m = max_residual_m;
            best_lower_count = lower_count;
            best_upper_count = upper_count;
        }
    }

    if (!best_valid) {
        return -1;
    }

    result->valid = true;
    result->used_anchor_count = uwb_tag_loc_popcount_u32(best_mask);
    result->lower_anchor_count = best_lower_count;
    result->upper_anchor_count = best_upper_count;
    result->x_mm = (int32_t)lround(best_estimate.x * 1000.0);
    result->y_mm = (int32_t)lround(best_estimate.y * 1000.0);
    result->z_mm = (int32_t)lround(best_estimate.z * 1000.0);
    result->residual_rms_mm = (uint32_t)lround(best_rms_m * 1000.0);
    result->residual_max_mm =
        (uint32_t)lround(best_max_residual_m * 1000.0);

    for (size_t i = 0U, out = 0U; i < candidate_count; ++i) {
        if ((best_mask & (1UL << i)) == 0U) {
            continue;
        }
        result->anchor_ids[out++] = candidates[i].anchor_id;
    }

    return 0;
}
