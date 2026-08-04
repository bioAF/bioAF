#' Capture an analysis snapshot and send to bioAF
#'
#' @param obj A Seurat object (or any R object: metadata extraction is best-effort)
#' @param label Human-readable label for this snapshot
#' @param notes Optional free-text notes
#' @param figure Optional file path to a figure to attach
#' @param save_checkpoint If TRUE, save metadata + embeddings as parquet
#' @param experiment_id Override default from BIOAF_EXPERIMENT_ID
#' @param project_id Override default from BIOAF_PROJECT_ID
#' @return Created snapshot response (list)
#' @export
bioaf_snapshot <- function(obj, label, notes = NULL, figure = NULL,
                           save_checkpoint = FALSE, experiment_id = NULL,
                           project_id = NULL) {
  # Extract metadata
  if (requireNamespace("Seurat", quietly = TRUE) && inherits(obj, "Seurat")) {
    metadata <- extract_seurat_metadata(obj)
  } else {
    metadata <- list(object_type = "seurat", cell_count = NULL, gene_count = NULL)
  }

  # Build payload
  payload <- list(
    label = label,
    object_type = metadata$object_type,
    cell_count = metadata$cell_count,
    gene_count = metadata$gene_count,
    parameters_json = metadata$parameters_json,
    embeddings_json = metadata$embeddings_json,
    clusterings_json = metadata$clusterings_json,
    layers_json = metadata$layers_json,
    metadata_columns_json = metadata$metadata_columns_json,
    command_log_json = metadata$command_log_json
  )

  if (!is.null(notes)) {
    payload$notes <- notes
  }

  # Resolve experiment/project IDs
  exp_id <- experiment_id %||% (if (nzchar(.bioaf_env$experiment_id)) as.integer(.bioaf_env$experiment_id) else NULL)
  proj_id <- project_id %||% (if (nzchar(.bioaf_env$project_id)) as.integer(.bioaf_env$project_id) else NULL)
  if (!is.null(exp_id)) payload$experiment_id <- exp_id
  if (!is.null(proj_id)) payload$project_id <- proj_id

  # Session ID
  if (nzchar(.bioaf_env$session_id)) {
    payload$notebook_session_id <- as.integer(.bioaf_env$session_id)
  }

  # Upload figure if provided
  if (!is.null(figure)) {
    tryCatch({
      fig_resp <- .bioaf_upload_figure(figure)
      if (!is.null(fig_resp$id)) {
        payload$figure_file_id <- fig_resp$id
      }
    }, error = function(e) {
      warning("Figure upload failed: ", e$message)
    })
  }

  # Upload checkpoint if requested
  if (save_checkpoint && inherits(obj, "Seurat")) {
    tryCatch({
      ckpt_resp <- .bioaf_upload_checkpoint(obj)
      if (!is.null(ckpt_resp$id)) {
        payload$checkpoint_file_id <- ckpt_resp$id
      }
    }, error = function(e) {
      warning("Checkpoint upload failed: ", e$message)
    })
  }

  .bioaf_post("/api/snapshots", body = payload)
}
