"""Tests for tabconverter."""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tabconverter as tc


class TestNoteConversion:
    def test_basic_notes(self):
        assert tc.note_to_semitones('C0') == 0
        assert tc.note_to_semitones('C1') == 12
        assert tc.note_to_semitones('E2') == 28
        assert tc.note_to_semitones('A2') == 33

    def test_sharps_and_flats(self):
        assert tc.note_to_semitones('C#2') == tc.note_to_semitones('Db2')
        assert tc.note_to_semitones('Bb1') == tc.note_to_semitones('A#1')

    def test_lowercase(self):
        assert tc.note_to_semitones('e4') == tc.note_to_semitones('E4')

    def test_invalid_note(self):
        with pytest.raises(ValueError):
            tc.note_to_semitones('H2')
        with pytest.raises(ValueError):
            tc.note_to_semitones('E')  # No octave

    def test_roundtrip(self):
        for note in ['C0', 'E2', 'A2', 'F#3', 'B4']:
            semi = tc.note_to_semitones(note)
            assert tc.note_to_semitones(tc.semitones_to_note(semi)) == semi


class TestTuningDetection:
    def test_detect_with_octaves(self):
        lines = [
            'Some Song Title',
            '',
            'E4|-----0---|',
            'B3|---1-----|',
            'G3|-2-------|',
        ]
        assert tc.detect_source_tuning(lines) == ['E4', 'B3', 'G3']

    def test_detect_stops_at_first_section(self):
        lines = [
            'E4|---|',
            'B3|---|',
            '',
            'A9|---|',  # Different later section must not pollute the tuning
        ]
        assert tc.detect_source_tuning(lines) == ['E4', 'B3']

    def test_repeated_notes_preserved(self):
        lines = ['E2|---|', 'E2|---|']
        assert tc.detect_source_tuning(lines) == ['E2', 'E2']

    def test_no_octaves_returns_none(self):
        lines = ['e|---|', 'B|---|', 'G|---|']
        assert tc.detect_source_tuning(lines) is None

    def test_infer_standard_guitar_from_bare_labels(self):
        lines = ['e|---|', 'B|---|', 'G|---|', 'D|---|', 'A|---|', 'E|---|']
        assert tc.infer_source_tuning(lines) == ['E4', 'B3', 'G3', 'D3', 'A2', 'E2']

    def test_infer_drop_d_guitar_from_bare_labels(self):
        lines = ['e|---|', 'B|---|', 'G|---|', 'D|---|', 'A|---|', 'D|---|']
        assert tc.infer_source_tuning(lines) == ['E4', 'B3', 'G3', 'D3', 'A2', 'D2']

    def test_infer_standard_bass_from_bare_labels(self):
        lines = ['G|---|', 'D|---|', 'A|---|', 'E|---|']
        assert tc.infer_source_tuning(lines) == ['G2', 'D2', 'A1', 'E1']

    def test_infer_5string_bass_from_bare_labels(self):
        lines = ['G|---|', 'D|---|', 'A|---|', 'E|---|', 'B|---|']
        assert tc.infer_source_tuning(lines) == ['G2', 'D2', 'A1', 'E1', 'B0']


class TestLabelMatching:
    GUITAR = ['E4', 'B3', 'G3', 'D3', 'A2', 'E2']
    GUITAR_LOW_TO_HIGH = ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']

    def test_full_labels(self):
        labels = ['E4', 'B3', 'G3', 'D3', 'A2', 'E2']
        assert tc.match_labels_to_strings(labels, self.GUITAR) == [0, 1, 2, 3, 4, 5]

    def test_bare_lowercase_labels_with_duplicate_e(self):
        # The common internet-tab convention: no octaves, lowercase high e.
        labels = ['e', 'B', 'G', 'D', 'A', 'E']
        assert tc.match_labels_to_strings(labels, self.GUITAR) == [0, 1, 2, 3, 4, 5]

    def test_bare_labels_against_low_to_high_source_tuning(self):
        # Regression test: -s E2,A2,D3,G3,B3,E4 must map top line 'e' to E4 (idx 5)
        # and bottom line 'E' to E2 (idx 0), not swap them!
        labels = ['e', 'B', 'G', 'D', 'A', 'E']
        assert tc.match_labels_to_strings(labels, self.GUITAR_LOW_TO_HIGH) == [5, 4, 3, 2, 1, 0]

    def test_unmatched_label(self):
        labels = ['e', 'B', 'X#']
        result = tc.match_labels_to_strings(labels, self.GUITAR)
        assert result[0] == 0 and result[1] == 1 and result[2] is None


class TestNoteExtraction:
    def test_simple_extraction(self):
        lines = [
            'A2|--3--12--|',
            'E2|-0-------|',
        ]
        sections = tc.extract_note_events(lines, ['A2', 'E2'])
        assert len(sections) == 1
        header, events = sections[0]
        assert header is None
        assert events[(2, 0)] == 3
        assert events[(5, 0)] == 12  # Multi-digit fret, anchored at its first digit
        assert events[(1, 1)] == 0

    def test_header_capture(self):
        lines = [
            'Chorus:',
            'A2|--3--|',
            '',
            'Bridge:',
            'A2|--5--|',
        ]
        sections = tc.extract_note_events(lines, ['A2'])
        assert sections[0][0] == 'Chorus:'
        assert sections[1][0] == 'Bridge:'

    def test_fret_above_max_ignored(self):
        lines = ['A2|--30--5--|']
        _, events = tc.extract_note_events(lines, ['A2'], max_fret=24)[0]
        assert list(events.values()) == [5]

    def test_max_fret_respected(self):
        lines = ['A2|--14--|']
        sections = tc.extract_note_events(lines, ['A2'], max_fret=12)
        assert sections == []

    def test_uneven_measure_widths_aligned_at_barlines(self):
        # Measure 1 on E1 is longer than on A1; measure 2 on A1 must not collide with measure 1 on E1
        lines = [
            'A1|----|2---|',
            'E1|--3---3-|----|',
        ]
        _, events = tc.extract_note_events(lines, ['A1', 'E1'])[0]
        a1_cols = [col for (col, s_idx) in events if s_idx == 0]
        e1_cols = [col for (col, s_idx) in events if s_idx == 1]
        assert max(e1_cols) < min(a1_cols)


class TestMelodyStrings:
    def test_explicit_config(self):
        config = {'melody_strings': [0, 1]}
        assert tc.get_melody_strings(['E2', 'A2', 'D3', 'G3'], config) == {0, 1}

    def test_melody_first_half(self):
        # stick classic order: melody group listed first
        tuning = ['D4', 'A3', 'E3', 'B2', 'F#2', 'C2', 'G2', 'D3', 'A3', 'E4']
        assert tc.get_melody_strings(tuning, {}) == {0, 1, 2, 3, 4}

    def test_melody_second_half(self):
        # matchedReciprocal order: bass group listed first
        tuning = ['Bb0', 'Eb1', 'Ab1', 'C#2', 'F#2', 'B2',
                  'Bb1', 'Eb2', 'Ab2', 'C#3', 'F#3', 'B3']
        assert tc.get_melody_strings(tuning, {}) == {6, 7, 8, 9, 10, 11}


class TestStringAllocation:
    CONFIG = {
        'target_tuning': ['E1', 'A1', 'D2', 'G2', 'B2', 'E3'],
        'max_fret': 24,
        'bass_max_fret': 12,
        'melody_min_fret': 0,
        'hand_separation': 0,
    }

    def test_bass_note_lands_on_bass_string(self):
        tuning = self.CONFIG['target_tuning']
        pitch = tc.note_to_semitones('A1')  # Open A on the bass side
        idx, fret = tc.find_best_target_string(pitch, 'bass', tuning, set(), self.CONFIG)
        assert idx is not None
        assert tc.parse_tuning(tuning)[idx] + fret == pitch

    def test_occupied_string_skipped(self):
        tuning = self.CONFIG['target_tuning']
        pitch = tc.note_to_semitones('A1')
        idx1, _ = tc.find_best_target_string(pitch, 'bass', tuning, set(), self.CONFIG)
        idx2, fret2 = tc.find_best_target_string(pitch, 'bass', tuning, {idx1}, self.CONFIG)
        assert idx2 != idx1
        assert tc.parse_tuning(tuning)[idx2] + fret2 == pitch

    def test_unreachable_note(self):
        idx, fret = tc.find_best_target_string(
            tc.note_to_semitones('C0'), 'bass', ['E3'], set(), {'max_fret': 24})
        assert idx is None

    def test_octave_shift_rescues_low_note(self):
        idx, fret = tc.try_octave_shifts(
            tc.note_to_semitones('C0'), 'bass', ['E1', 'A1'], set(),
            {'max_fret': 24, 'bass_max_fret': 12})
        assert idx is not None


class TestConfig:
    def test_bundled_tuning_by_name(self):
        config = tc.load_config('guitar/6string/standard')
        assert config['target_tuning'] == ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']

    def test_bundled_tunings_all_valid(self):
        for name in tc.list_bundled_tunings():
            config = tc.load_config(name)
            semis = tc.parse_tuning(config['target_tuning'])
            if name.startswith('stick/') and len(semis) >= 10:
                mel = tc.get_melody_strings(config['target_tuning'], config)
                bass = set(range(len(semis))) - mel
                avg_mel = sum(semis[i] for i in mel) / len(mel)
                avg_bass = sum(semis[i] for i in bass) / len(bass)
                assert avg_mel > avg_bass, f"{name} has bass strings higher than melody strings"

    def test_shorthand_aliases_and_inline_tuning(self):
        assert tc.load_config('dropd')['target_tuning'] == ['D2', 'A2', 'D3', 'G3', 'B3', 'E4']
        assert tc.load_config('D2,A2,D3,G3,A3,D4')['target_tuning'] == ['D2', 'A2', 'D3', 'G3', 'A3', 'D4']
        assert tc.parse_source_tuning_arg('bass') == ['E1', 'A1', 'D2', 'G2']
        assert tc.parse_source_tuning_arg('E,A,D,G') == ['E1', 'A1', 'D2', 'G2']

    def test_missing_config(self):
        with pytest.raises(FileNotFoundError):
            tc.resolve_config_path('nonexistent/tuning')

    def test_missing_target_tuning_key(self, tmp_path):
        p = tmp_path / 'bad.json'
        p.write_text('{"max_fret": 24}')
        with pytest.raises(ValueError):
            tc.load_config(p)

    def test_invalid_note_in_tuning(self, tmp_path):
        p = tmp_path / 'bad.json'
        p.write_text('{"target_tuning": ["E2", "H3"]}')
        with pytest.raises(ValueError):
            tc.load_config(p)


class TestEndToEnd:
    def make_inputs(self, tmp_path):
        bass = tmp_path / 'bass.txt'
        bass.write_text(
            'Riff:\n'
            'G2|---------|\n'
            'D2|---------|\n'
            'A1|--0--2---|\n'
            'E1|---------|\n')
        guitar = tmp_path / 'guitar.txt'
        guitar.write_text(
            'Riff:\n'
            'E4|---------|\n'
            'B3|--5--7---|\n'
            'G3|---------|\n'
            'D3|---------|\n'
            'A2|---------|\n'
            'E2|---------|\n')
        return bass, guitar

    def test_merge(self, tmp_path, capsys):
        bass, guitar = self.make_inputs(tmp_path)
        config = tc.load_config('stick/10string/classic')
        out = tmp_path / 'out.tab'
        rc = tc.merge_tab_files([bass, guitar], out, config)
        assert rc == 0
        text = out.read_text()
        assert 'Riff:' in text
        # 10 tab lines + header
        tab_lines = [l for l in text.splitlines() if '|' in l]
        assert len(tab_lines) == 10
        # All 4 notes should be placed (no X markers)
        assert 'X' not in text
        total_notes = sum(sum(c.isdigit() for c in l.split('|', 1)[1]) for l in tab_lines)
        assert total_notes >= 4

    def test_merge_to_stdout(self, tmp_path, capsys):
        bass, guitar = self.make_inputs(tmp_path)
        config = tc.load_config('stick/10string/classic')
        rc = tc.merge_tab_files([bass, guitar], None, config)
        assert rc == 0
        assert 'Riff:' in capsys.readouterr().out

    def test_lowercase_labels_with_explicit_tuning(self, tmp_path):
        guitar = tmp_path / 'guitar.txt'
        guitar.write_text(
            'e|--5------|\n'
            'B|---------|\n'
            'G|---------|\n'
            'D|---------|\n'
            'A|---------|\n'
            'E|--0------|\n')
        config = tc.load_config('guitar/6string/standard')
        out = tmp_path / 'out.tab'
        rc = tc.merge_tab_files(
            [guitar], out, config,
            source_tunings_list=[['E4', 'B3', 'G3', 'D3', 'A2', 'E2']])
        assert rc == 0
        # Both E-string notes must survive as two distinct placed notes at
        # the same column — if the duplicate 'e'/'E' labels were collapsed
        # onto one string, one event would overwrite the other.
        text = out.read_text()
        placed = re.findall(r'\d+', text)
        assert len(placed) == 2

    def test_no_sections_errors(self, tmp_path, capsys):
        empty = tmp_path / 'empty.txt'
        empty.write_text('just some text\n')
        config = tc.load_config('guitar/6string/standard')
        rc = tc.merge_tab_files(
            [empty], tmp_path / 'out.tab', config,
            source_tunings_list=[['E2', 'A2', 'D3', 'G3', 'B3', 'E4']])
        assert rc == 1

    def test_zero_config_bare_label_tabs(self, tmp_path):
        # Internet-style tabs with no octave numbers and no -s flag
        bass = tmp_path / 'bass.tab'
        bass.write_text(
            'Chorus:\n'
            'G|---------|\n'
            'D|---------|\n'
            'A|--3--5---|\n'
            'E|---------|\n'
        )
        guitar = tmp_path / 'guitar.tab'
        guitar.write_text(
            'Chorus:\n'
            'e|---------|\n'
            'B|---------|\n'
            'G|--5--7---|\n'
            'D|--5--7---|\n'
            'A|--3--5---|\n'
            'E|---------|\n'
        )
        out = tmp_path / 'stick.tab'
        rc = tc.merge_tab_files([bass, guitar], out, tc.load_config('stick'))
        assert rc == 0
        text = out.read_text()
        assert 'Chorus:' in text
        assert 'X' not in text

    def test_header_alignment_across_mismatched_sections(self, tmp_path):
        bass = tmp_path / 'bass.tab'
        bass.write_text(
            'Intro:\n'
            'G2|------|\n'
            'D2|------|\n'
            'A1|--0---|\n'
            'E1|------|\n\n'
            'Verse:\n'
            'G2|------|\n'
            'D2|------|\n'
            'A1|--2---|\n'
            'E1|------|\n\n'
            'Chorus:\n'
            'G2|------|\n'
            'D2|------|\n'
            'A1|--3---|\n'
            'E1|------|\n'
        )
        guitar = tmp_path / 'guitar.tab'
        guitar.write_text(
            'Intro:\n'
            'E4|------|\n'
            'B3|--5---|\n'
            'G3|------|\n'
            'D3|------|\n'
            'A2|------|\n'
            'E2|------|\n\n'
            'Chorus:\n'
            'E4|--8---|\n'
            'B3|------|\n'
            'G3|------|\n'
            'D3|------|\n'
            'A2|------|\n'
            'E2|------|\n'
        )
        out = tmp_path / 'merged.tab'
        rc = tc.merge_tab_files([bass, guitar], out, tc.load_config('stick/10string/classic'))
        assert rc == 0
        blocks = out.read_text().strip().split('\n\n')
        assert len(blocks) == 3
        assert blocks[0].startswith('Intro:')
        assert blocks[1].startswith('Verse:')
        assert blocks[2].startswith('Chorus:')
        # Verse block has only 1 note (bass), while Chorus block has 2 notes (bass + guitar)
        verse_digits = re.findall(r'\d+', '\n'.join(blocks[1].splitlines()[1:]))
        chorus_digits = re.findall(r'\d+', '\n'.join(blocks[2].splitlines()[1:]))
        assert len(verse_digits) == 1
        assert len(chorus_digits) == 2

    def test_exact_pitch_preferred_over_octave_shift(self):
        # Pitch A2 (33) on classic 10-string stick is fret 3 on F#2 (30).
        # Even though melody_min_fret is 7, it must play fret 3 on F#2 (exact pitch 33)
        # instead of transposing +12 semitones to A3!
        config = tc.load_config('stick/10string/classic')
        tuning = config['target_tuning']
        pitch = tc.note_to_semitones('A2')
        idx, fret = tc.try_octave_shifts(pitch, 'melody', tuning, set(), config)
        assert idx is not None
        assert tc.parse_tuning(tuning)[idx] + fret == pitch

