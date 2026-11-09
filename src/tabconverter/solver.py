"""Fretboard allocation solver, voice-leading cost model, and section alignment."""

import re
from collections import defaultdict

from .music import get_melody_strings, is_split_hand_config, parse_tuning


def find_best_target_string(note_pitch, part_type, target_tuning, occupied_strings,
                            config, other_hand_frets=None, prefer_melody_strings=True,
                            prev_fret=None, prev_string=None, chord_frets=None,
                            relax_fret_bounds=False, relax_hand_separation=False,
                            single_part_mode=False, linked_target_string=None):
    """Find the best target string for a note considering playability, voice leading, and chord shapes.

    Args:
        note_pitch: Absolute pitch of the note in semitones
        part_type: 'bass' or 'melody'
        target_tuning: List of target tuning notes
        occupied_strings: Set of string indices already used at this timestamp
        config: Config dict with fret constraints
        other_hand_frets: Dict of {string_idx: fret} for the other hand at this timestamp
        prefer_melody_strings: If True, prefer melody strings for melody parts
        prev_fret: Previous fret played by this hand (for smooth voice leading)
        prev_string: Previous string index played by this hand for the same pitch
        chord_frets: Dict of {string_idx: fret} already placed for THIS hand at this column
        relax_fret_bounds: If True, allow [0, max_fret] with a soft penalty outside [fret_min, fret_max]
        relax_hand_separation: If True, treat hand_separation on non-overlapping string groups as a soft penalty
        single_part_mode: If True and config has no split-hand constraints, don't penalize half the strings
        linked_target_string: If set (for hammer-on/pull-off/slide), strongly prefer keeping the note on this string

    Returns:
        (target_string_idx, fret) or (None, None) if impossible
    """
    target_semi = parse_tuning(target_tuning)
    num_strings = len(target_semi)
    melody_strings = get_melody_strings(target_tuning, config)

    max_fret = config.get('max_fret', 24)
    hand_separation = config.get('hand_separation', 4)

    if part_type == 'bass':
        fret_min = 0
        fret_max = config.get('bass_max_fret', 12)
        prefer_low_strings = True
    else:
        fret_min = config.get('melody_min_fret', 7)
        fret_max = max_fret
        prefer_low_strings = False

    split_mode = not (single_part_mode and not is_split_hand_config(config))

    candidates = []

    for tgt_idx in range(num_strings):
        if tgt_idx in occupied_strings:
            continue

        tgt_pitch = target_semi[tgt_idx]
        fret = note_pitch - tgt_pitch

        if fret < 0 or fret > max_fret:
            continue

        # If linked via legato/slide to a specific string, allow full [0, max_fret] on that string
        is_linked_string = (linked_target_string is not None and tgt_idx == linked_target_string)

        range_penalty = 0.0
        if fret < fret_min or fret > fret_max:
            if not (relax_fret_bounds or is_linked_string):
                continue
            if fret < fret_min:
                range_penalty = (fret_min - fret) * 2.5 + 8.0
            else:
                range_penalty = (fret - fret_max) * 2.5 + 8.0

        is_melody_string = tgt_idx in melody_strings

        sep_penalty = 0.0
        if other_hand_frets and hand_separation > 0:
            collision = False
            for other_string, other_fret in other_hand_frets.items():
                fret_gap = abs(fret - other_fret)
                if fret_gap < hand_separation:
                    same_region = (other_string in melody_strings) == is_melody_string
                    if not (relax_hand_separation or is_linked_string) or same_region:
                        collision = True
                        break
                    else:
                        sep_penalty = max(sep_penalty, (hand_separation - fret_gap) * 4.0)
            if collision:
                continue

        if not split_mode:
            region_penalty = 0.0
        elif prefer_low_strings and not is_melody_string:
            region_penalty = 0.0
        elif prefer_low_strings and is_melody_string:
            region_penalty = 100.0
        elif not prefer_low_strings and is_melody_string:
            region_penalty = 0.0 if prefer_melody_strings else 20.0
        elif not prefer_low_strings and not is_melody_string:
            region_penalty = 100.0 if prefer_melody_strings else 0.0
        else:
            region_penalty = 0.0

        if not split_mode:
            fret_center = min(7.0, max_fret / 3)
        elif prefer_low_strings:
            fret_center = min(5.0, (fret_min + fret_max) / 2)
        else:
            fret_center = min(fret_min + 3.0, (fret_min + fret_max) / 2)
        fret_penalty = abs(fret - fret_center) * 0.1

        continuity_penalty = 0.0
        if prev_fret is not None:
            continuity_penalty += abs(fret - prev_fret) * 0.35
            if prev_string is not None and tgt_idx == prev_string and fret == prev_fret:
                continuity_penalty -= 1.0

        legato_bonus = -50.0 if is_linked_string else 0.0

        chord_penalty = 0.0
        if chord_frets:
            frets_in_chord = list(chord_frets.values())
            avg_chord_fret = sum(frets_in_chord) / len(frets_in_chord)
            max_span = max(abs(fret - cf) for cf in frets_in_chord if cf != 0 or fret != 0) if frets_in_chord else 0
            chord_penalty += abs(fret - avg_chord_fret) * 0.8
            if max_span > 4 and fret > 0:
                chord_penalty += (max_span - 4) * 6.0
            min_str_dist = min(abs(tgt_idx - cs) for cs in chord_frets.keys())
            chord_penalty += (min_str_dist - 1) * 0.5

        score = (
            region_penalty
            + range_penalty
            + sep_penalty
            + fret_penalty
            + continuity_penalty
            + chord_penalty
            + legato_bonus
        )
        candidates.append((score, tgt_idx, fret))

    if not candidates:
        return None, None

    candidates.sort()
    _, best_idx, best_fret = candidates[0]
    return best_idx, best_fret


def try_octave_shifts(note_pitch, part_type, target_tuning, occupied_strings,
                     config, other_hand_frets=None, prefer_melody_strings=True,
                     prev_fret=None, prev_string=None, chord_frets=None,
                     single_part_mode=False, linked_target_string=None):
    """Find a playable position, relaxing fret bounds before resorting to octave shifts."""
    melody_strings = get_melody_strings(target_tuning, config)
    prefer_low = (part_type == 'bass')
    split_mode = not (single_part_mode and not is_split_hand_config(config))

    def in_pref_region(idx):
        if idx is None:
            return False
        if not split_mode:
            return True
        is_mel = idx in melody_strings
        return (prefer_low and not is_mel) or (not prefer_low and is_mel)

    # 1. Exact pitch, strict fret bounds & strict hand separation in preferred region
    result = find_best_target_string(
        note_pitch, part_type, target_tuning, occupied_strings, config,
        other_hand_frets, prefer_melody_strings,
        prev_fret=prev_fret, prev_string=prev_string, chord_frets=chord_frets,
        relax_fret_bounds=False, relax_hand_separation=False,
        single_part_mode=single_part_mode, linked_target_string=linked_target_string,
    )
    if in_pref_region(result[0]):
        return result

    # 2. Exact pitch in preferred region with relaxed fret bounds / separate-group hand separation
    relaxed_exact = find_best_target_string(
        note_pitch, part_type, target_tuning, occupied_strings, config,
        other_hand_frets, prefer_melody_strings,
        prev_fret=prev_fret, prev_string=prev_string, chord_frets=chord_frets,
        relax_fret_bounds=True, relax_hand_separation=True,
        single_part_mode=single_part_mode, linked_target_string=linked_target_string,
    )
    if in_pref_region(relaxed_exact[0]):
        return relaxed_exact

    # 3. Try ±1 and ±2 octave shifts in preferred region
    for relax in (False, True):
        for octave_shift in (12, -12, 24, -24):
            shifted = find_best_target_string(
                note_pitch + octave_shift, part_type, target_tuning, occupied_strings,
                config, other_hand_frets, prefer_melody_strings,
                prev_fret=prev_fret, prev_string=None, chord_frets=chord_frets,
                relax_fret_bounds=relax, relax_hand_separation=relax,
                single_part_mode=single_part_mode, linked_target_string=linked_target_string,
            )
            if in_pref_region(shifted[0]):
                return shifted

    # 4. Fallback: accept exact pitch in any region
    if result[0] is not None:
        return result
    if relaxed_exact[0] is not None:
        return relaxed_exact

    # 5. Last resort: any octave shift in any region
    for relax in (False, True):
        for octave_shift in (12, -12, 24, -24):
            shifted = find_best_target_string(
                note_pitch + octave_shift, part_type, target_tuning, occupied_strings,
                config, other_hand_frets, prefer_melody_strings,
                prev_fret=prev_fret, prev_string=None, chord_frets=chord_frets,
                relax_fret_bounds=relax, relax_hand_separation=relax,
                single_part_mode=single_part_mode, linked_target_string=linked_target_string,
            )
            if shifted[0] is not None:
                return shifted

    return None, None


def normalize_section_header(header):
    """Normalize a section header for cross-file matching (e.g. 'Bridge (Repeat 3x):' -> 'bridge')."""
    if not header:
        return None
    s = header.strip().lower().rstrip(':')
    s = re.sub(r'\([^)]*\)', '', s)
    s = re.sub(r'\b(section|part|riff)\s*#?\d+\b', '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    if s.startswith('intro'):
        return 'intro'
    if s.startswith('outro'):
        return 'outro'
    if 'solo' in s:
        return 'solo'
    if 'chorus' in s and 'pre' not in s:
        return 'chorus'
    if 'pre' in s and 'chorus' in s:
        return 'pre-chorus'
    if 'verse' in s and 'pre' not in s:
        return 'verse'
    if 'bridge' in s:
        return 'bridge'
    return s or None


def align_part_sections(all_parts):
    """Align sections across multiple input parts using LCS on normalized section headers."""
    num_parts = len(all_parts)
    if num_parts == 0:
        return []
    if num_parts == 1:
        return [[(0, sec)] for sec in all_parts[0][2]]

    section_lists = [part[2] for part in all_parts]
    counts = [len(secs) for secs in section_lists]

    annotated = []
    for secs in section_lists:
        part_keys = []
        current_key = None
        sub_counts = defaultdict(int)
        for sec_idx, sec in enumerate(secs):
            raw_header = sec['header'] if isinstance(sec, dict) else sec[0]
            norm = normalize_section_header(raw_header)
            if norm is not None:
                if norm != current_key:
                    current_key = norm
                    sub_counts[current_key] = 0
                else:
                    sub_counts[current_key] += 1
            elif current_key is not None:
                sub_counts[current_key] += 1
            else:
                current_key = f"__sec_{sec_idx}"
                sub_counts[current_key] = 0
            part_keys.append((current_key, sub_counts[current_key]))
        annotated.append(part_keys)

    if len(set(counts)) == 1 and annotated[0] == annotated[1]:
        return [
            [(p_idx, section_lists[p_idx][s_idx]) for p_idx in range(num_parts)]
            for s_idx in range(counts[0])
        ]

    has_any_named_header = any(
        (sec['header'] if isinstance(sec, dict) else sec[0]) is not None
        for secs in section_lists
        for sec in secs
    )
    if not has_any_named_header:
        max_len = max(counts)
        slots = []
        for s_idx in range(max_len):
            slot = [
                (p_idx, section_lists[p_idx][s_idx])
                for p_idx in range(num_parts)
                if s_idx < len(section_lists[p_idx])
            ]
            slots.append(slot)
        return slots

    aligned = [(annotated[0][i], [(0, section_lists[0][i])]) for i in range(len(section_lists[0]))]

    for p_idx in range(1, num_parts):
        other_secs = section_lists[p_idx]
        other_keys = annotated[p_idx]
        m, n = len(aligned), len(other_secs)

        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(m - 1, -1, -1):
            for j in range(n - 1, -1, -1):
                if aligned[i][0] == other_keys[j]:
                    dp[i][j] = 1 + dp[i + 1][j + 1]
                else:
                    dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

        if dp[0][0] == 0:
            new_aligned = []
            for k in range(max(m, n)):
                items = []
                key = None
                if k < m:
                    key = aligned[k][0]
                    items.extend(aligned[k][1])
                if k < n:
                    if key is None:
                        key = other_keys[k]
                    items.append((p_idx, other_secs[k]))
                new_aligned.append((key, items))
            aligned = new_aligned
        else:
            new_aligned = []
            i = j = 0
            while i < m and j < n:
                if aligned[i][0] == other_keys[j]:
                    combined = list(aligned[i][1]) + [(p_idx, other_secs[j])]
                    new_aligned.append((aligned[i][0], combined))
                    i += 1
                    j += 1
                elif dp[i + 1][j] >= dp[i][j + 1]:
                    new_aligned.append(aligned[i])
                    i += 1
                else:
                    new_aligned.append((other_keys[j], [(p_idx, other_secs[j])]))
                    j += 1
            while i < m:
                new_aligned.append(aligned[i])
                i += 1
            while j < n:
                new_aligned.append((other_keys[j], [(p_idx, other_secs[j])]))
                j += 1
            aligned = new_aligned

    return [items for _, items in aligned]
