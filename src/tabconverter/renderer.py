"""ASCII tablature renderer with barlines, playing techniques, and line wrapping."""

import re

from .music import get_display_string_order


def _wrap_rendered_system(labels, content_slices_per_string, barline_slice_indices, wrap_width):
    """Split a rendered section's column slices into multiple systems of at most wrap_width chars."""
    if not content_slices_per_string or not content_slices_per_string[0]:
        return [f"{lbl}|" for lbl in labels]

    prefix_width = len(labels[0]) + 1  # label + '|'
    budget = max(16, wrap_width - prefix_width)

    num_slices = len(content_slices_per_string[0])
    slice_widths = [len(content_slices_per_string[0][k]) for k in range(num_slices)]
    total_content_width = sum(slice_widths)
    if total_content_width <= budget:
        return [
            f"{lbl}|{''.join(slices)}"
            for lbl, slices in zip(labels, content_slices_per_string)
        ]

    # Find split points (preferring barline slice indices) so each chunk <= budget
    chunks = []
    start = 0
    while start < num_slices:
        acc = 0
        last_bar = None
        end = start
        while end < num_slices and acc + slice_widths[end] <= budget:
            acc += slice_widths[end]
            if end in barline_slice_indices and end > start:
                last_bar = end + 1
            end += 1

        if end == start:
            end = start + 1
        elif end < num_slices and last_bar is not None and last_bar > start:
            end = last_bar

        chunks.append((start, end))
        start = end

    out_lines = []
    for c_idx, (s_start, s_end) in enumerate(chunks):
        if c_idx > 0:
            out_lines.append("")
        for lbl, slices in zip(labels, content_slices_per_string):
            out_lines.append(f"{lbl}|{''.join(slices[s_start:s_end])}")
    return out_lines


def render_merged_sections(merged_sections, target_tuning_list, config, wrap_width=None):
    """Render allocated sections into formatted ASCII tablature text.

    Args:
        merged_sections: List of tuples:
            (section_header, target_section, max_col, barlines[, target_techniques])
        target_tuning_list: List of target tuning note names
        config: Target config dict
        wrap_width: Optional maximum line width (e.g. 80) for wrapping wide sections

    Returns:
        Formatted tab string ending with a newline.
    """
    num_target_strings = len(target_tuning_list)
    target_displays = [re.sub(r'\d+', '', note) for note in target_tuning_list]
    max_label_width = max(len(d) for d in target_displays)
    display_order = get_display_string_order(target_tuning_list, config)
    ordered_labels = [target_displays[tgt_idx].ljust(max_label_width) for tgt_idx in display_order]

    merged_lines = []

    for section_idx, sec_tuple in enumerate(merged_sections):
        if len(sec_tuple) == 5:
            section_header, target_section, max_col, barlines, target_techniques = sec_tuple
        else:
            section_header, target_section, max_col, barlines = sec_tuple
            target_techniques = {}

        if section_idx > 0:
            merged_lines.append("")

        if section_header:
            merged_lines.append(section_header)

        # Build formatted token at each (tgt_idx, col) and connectors (e.g. 'h', 'p', '/', '\')
        formatted_tokens = {}  # (tgt_idx, col) -> str
        connector_tokens = {}  # (tgt_idx, col) -> str

        for tgt_idx, col_map in target_section.items():
            for col, fret in col_map.items():
                tech = target_techniques.get((tgt_idx, col))
                if tech is not None:
                    tok = tech.format_fret(fret)
                    if tech.connector_before and col > 0:
                        # Place connector ('h', 'p', '/', '\') in the column immediately preceding col
                        # if that preceding column was the connector column on the same target string
                        conn_col = col - 1
                        if conn_col not in col_map and conn_col not in barlines:
                            connector_tokens[(tgt_idx, conn_col)] = tech.connector_before
                else:
                    tok = str(fret)
                formatted_tokens[(tgt_idx, col)] = tok

        col_widths = {}
        has_event_at_col = {}
        for col in range(max_col + 1):
            max_width = 1
            any_event = False
            for tgt_idx in range(num_target_strings):
                if (tgt_idx, col) in formatted_tokens:
                    any_event = True
                    max_width = max(max_width, len(formatted_tokens[(tgt_idx, col)]))
                elif (tgt_idx, col) in connector_tokens:
                    any_event = True
            col_widths[col] = max_width
            has_event_at_col[col] = any_event

        # Absorb trailing empty spacer columns after multi-character tokens (2-digit frets, bends, vibrato)
        # so multi-char tokens don't inflate the tab width compared to the input
        skip_cols = set()
        for col in range(max_col + 1):
            if col in skip_cols:
                continue
            extra = col_widths[col] - 1
            for offset in range(1, extra + 1):
                next_col = col + offset
                if (
                    next_col <= max_col
                    and not has_event_at_col.get(next_col, False)
                    and next_col not in barlines
                ):
                    skip_cols.add(next_col)
                else:
                    break

        content_slices_per_string = [[] for _ in display_order]
        barline_slice_indices = set()
        slice_idx = 0

        for col in range(max_col + 1):
            if col in skip_cols:
                continue
            col_width = col_widths[col]
            is_barline_slice = (col in barlines) and not has_event_at_col.get(col, False)
            if is_barline_slice:
                barline_slice_indices.add(slice_idx)

            for row_i, tgt_idx in enumerate(display_order):
                if (tgt_idx, col) in formatted_tokens:
                    tok = formatted_tokens[(tgt_idx, col)]
                    content_slices_per_string[row_i].append(tok.ljust(col_width, '-'))
                elif (tgt_idx, col) in connector_tokens:
                    conn = connector_tokens[(tgt_idx, col)]
                    content_slices_per_string[row_i].append(conn.ljust(col_width, '-'))
                elif col in barlines:
                    content_slices_per_string[row_i].append('|')
                else:
                    content_slices_per_string[row_i].append('-' * col_width)

            slice_idx += 1

        if wrap_width and wrap_width > 0:
            merged_lines.extend(
                _wrap_rendered_system(
                    ordered_labels, content_slices_per_string, barline_slice_indices, wrap_width
                )
            )
        else:
            for lbl, slices in zip(ordered_labels, content_slices_per_string):
                merged_lines.append(f"{lbl}|{''.join(slices)}")

    return '\n'.join(merged_lines) + '\n'
