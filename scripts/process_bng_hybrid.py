#!/usr/bin/env python3

import os
import io
import re
import argparse
import itertools
import collections as col
import operator as op
import pickle as pck

import pandas as pd


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--agp-file',
        '-a',
        type=str,
        dest='agp',
        help='AGP assembly layout file generated by Bionano tool set for hybrid assembly.'
    )
    parser.add_argument(
        '--fasta-file',
        '-f',
        type=str,
        dest='fasta',
        help='FASTA file containig scaffold sequences generated by Bionano tool set for hybrid assembly.'
    )
    parser.add_argument('--no-fasta-cache', action='store_true', default=False, dest='no_fasta_cache')
    parser.add_argument(
        '--bed-file',
        '-b',
        type=str,
        dest='bed',
        help='Contig-to-reference alignments (unfiltered) of original contigs used as input to hybrid scaffolding.'
    )
    parser.add_argument(
        '--output',
        '-o',
        type=str,
        dest='output',
        help='Specify output prefix (directories will be created). Default: $PWD/bng_hybrid',
        default=os.path.join(os.getcwd(), 'bng_hybrid')
    )
    args = parser.parse_args()
    return args



def characterize_scaffold_sequence(sequence, scaffold):

    bases = re.compile('([ACGT]+|N+)')

    order = 0

    entities = []

    gap_size = 0
    cuts_per_gap = 0

    for mobj in re.finditer(bases, sequence.upper()):
        start = mobj.start()
        end = mobj.end()
        length = end - start
        if length == 6 and sequence[start:end].upper() == 'CTTAAG':
            # restriction site, only detected in BNG bridges
            gap_size += length
            cuts_per_gap += 1
            continue
        elif sequence[start].upper() == 'N':
            # gap, can be BNG bridge or assembler gap
            gap_size += length
            continue
        else:
            # must be genomic sequence,
            # can be fully processed right away
            if gap_size > 0:
                order += 1
                entities.append(
                    (
                        scaffold,
                        'gap',
                        order,
                        -1,
                        -1,
                        gap_size,
                        cuts_per_gap,
                        None
                    )
                )
                gap_size = 0
                cuts_per_gap = 0

            order += 1
            entities.append(
                (
                    scaffold,
                    'sequence',
                    order,
                    start,
                    end,
                    length,
                    -1,
                    col.Counter(sequence[start:end])
                )
            )
    return entities


def parse_fasta_scaffolds(fasta_path):

    current_seq = ''
    current_order = 0
    current_scaffold = ''

    seq_store = dict()

    entities = []

    with open(fasta_path, 'r') as fasta:
        for ln, line in enumerate(fasta, start=1):
            if line.startswith('>'):
                next_scaffold = line.strip().strip('>')
                if current_seq:
                    seq_store[current_scaffold] = current_seq
                    entities.append(
                        (
                            'scaffold',
                            current_scaffold,
                            current_order,
                            0,
                            len(current_seq),
                            len(current_seq),
                            -1,
                            col.Counter(current_seq)
                        )
                    )
                    sub_entities = characterize_scaffold_sequence(current_seq, current_scaffold)
                    entities.extend(sub_entities)

                current_scaffold = next_scaffold
                current_order = ln
                current_seq = ''
            else:
                current_seq += line.strip()

    if current_seq:
        seq_store[current_scaffold] = current_seq
        entities.append(
            (
                'scaffold',
                current_scaffold,
                current_order,
                0,
                len(current_seq),
                len(current_seq),
                -1,
                col.Counter(current_seq)
            )
        )
        sub_entities = characterize_scaffold_sequence(current_seq, current_scaffold)
        entities.extend(sub_entities)

    get_nuc_counts = op.itemgetter(*('A', 'C', 'G', 'T', 'a', 'c', 'g', 't', 'N', 'n'))

    rows = []

    for e in entities:
        if e[1] == 'gap':
            counts = [-1] * 10
        else:
            counts = list(get_nuc_counts(e[-1]))
        infos = list(e[:7]) + counts
        rows.append(infos)

    df = pd.DataFrame(
        rows,
        columns=[
            'object',
            'component',
            'order',
            'start',
            'end',
            'length',
            'cut_sites',
            'A', 'C', 'G', 'T', 'a', 'c', 'g', 't', 'N', 'n'
        ]
    )

    df = fill_in_gap_coordinates(df)

    return df, seq_store


def load_cached_fasta_data(scaffolds, sequences):

    with pd.HDFStore(scaffolds, 'r') as hdf:
        df = hdf['/cache']

    with open(sequences, 'rb') as dump:
        seq_store = pck.load(dump)

    return df, seq_store


def load_fasta_scaffolds(fasta_path, output_prefix, no_caching):

    if no_caching:
        fasta_scaffolds, seq_store = parse_fasta_scaffolds(fasta_path)
    else:
        scaffold_cache = output_prefix + '.cache.layout.h5'
        seq_cache = output_prefix + '.cache.seqs.pck'
        if os.path.isfile(scaffold_cache) and os.path.isfile(seq_cache):
            fasta_scaffolds, seq_store = load_cached_fasta_data(scaffold_cache, seq_cache)
        else:
            fasta_scaffolds, seq_store = parse_fasta_scaffolds(fasta_path)
            with open(seq_cache, 'wb') as dump:
                pck.dump(seq_store, dump)
            with pd.HDFStore(scaffold_cache, 'w') as hdf:
                hdf.put('cache', fasta_scaffolds, format='fixed')

    return fasta_scaffolds, seq_store


def fill_in_gap_coordinates(fasta_layout):

    rows = []
    starts = []
    ends = []

    for idx in fasta_layout.loc[fasta_layout['component'] == 'gap', :].index.values:
        rows.append(idx)
        starts.append(fasta_layout.at[idx-1, 'end'])
        ends.append(fasta_layout.at[idx+1, 'start'])

    fasta_layout.loc[rows, 'start'] = starts
    fasta_layout.loc[rows, 'end'] = ends
    
    return fasta_layout


def parse_agp_layout(agp_path):

    agp_header = [
        'object_name',
        'object_start',
        'object_end',
        'comp_number',
        'comp_type',
        'comp_name_OR_gap_length',
        'comp_start_OR_gap_type',
        'comp_end_OR_linkage',
        'comp_orient_OR_linkage_evidence'
    ]

    df = pd.read_csv(agp_path, sep='\t', comment='#', names=agp_header)
    # hopfully, all AGP files are simple in structure
    assert len(set(df['comp_type'].values).union(set(['W', 'N']))) == 2, 'Unexpected component type'
    assert df['comp_end_OR_linkage'].str.match('([0-9]+|yes)').all(), 'Unexpected linkage type'
    assert df['comp_start_OR_gap_type'].str.match('([0-9]+|scaffold)').all(), 'Unexpected gap type'
    return df


def compute_bng_contig_support(agp_layout):

    supported = []
    unsupported = []
    contig_names = []

    contig_to_scaffold = col.defaultdict(list)
    scaffold_to_contig = col.defaultdict(list)

    unsupported_broken = col.Counter()

    for idx, row in agp_layout.iterrows():
        if row['comp_type'] == 'N':
            continue
        else:
            contig_name = row['comp_name_OR_gap_length']
            if 'subseq' in contig_name:
                contig_name = contig_name.split('_subseq_')[0]

            if 'Super-Scaffold' not in row['object_name']:
                # unscaffolded sequence
                if 'subseq' in row['comp_name_OR_gap_length']:
                    # happens that multiple fragments of a contig
                    # appear as unsupported / unscaffolded for
                    # whatever reason
                    unsupported_broken[contig_name] += 1

                supported.append(0)
                unsupported.append(int(row['comp_end_OR_linkage']))
            else:
                contig_to_scaffold[contig_name].append(row['object_name'])
                scaffold_to_contig[row['object_name']].append(contig_name)

                supported.append(int(row['comp_end_OR_linkage']))
                unsupported.append(0)

            contig_names.append(contig_name)

    df = pd.DataFrame(
        [contig_names, supported, unsupported],
        index=[
            'contig_name',
            'BNG_supported',
            'BNG_unsupported'
        ]
    )
    df = df.transpose()

    contig_counts = df['contig_name'].value_counts()
    df = df.groupby('contig_name')[['BNG_supported', 'BNG_unsupported']].sum()
    df['contig_name'] = df.index.values
    df.reset_index(drop=True, inplace=True)
    df['contig_breaks'] = df['contig_name'].apply(lambda x: contig_counts[x] - 1)

    # no clue why, but some contigs are broken despite being unsupported
    # cluster10_contig_270_subseq_1:79636_obj
    # cluster10_contig_270_subseq_79637:120374_obj
    # ---> cluster10_contig_270    120374
    # so fix that here
    df.loc[df['BNG_supported'] == 0, 'contig_breaks'] = 0

    # now fix cases where a single contig has several BNG unsupported
    # fragments, which would otherwise be counted multiple times
    for ctg, broken_count in unsupported_broken.most_common():
        if broken_count < 2:
            break
        # count several "unsupported" fragments as one
        unsupported_breaks = broken_count - 1
        counted_breaks = int(df.loc[df['contig_name'] == ctg, 'contig_breaks'])
        if counted_breaks > 0:
            # avoids clash/duplicates together with first fix
            df.loc[df['contig_name'] == ctg, 'contig_breaks'] -= unsupported_breaks

    return df, contig_to_scaffold, scaffold_to_contig


def parse_contig_alignments(bed_path):

    bed_columns = [
        'chrom',
        'start',
        'end',
        'contig',
        'mapq',
        'strand'
    ]

    df = pd.read_csv(bed_path, sep='\t', names=bed_columns, header=None)
    df['chrom'] = df['chrom'].apply(lambda x: x.split('_')[0])

    df['length'] = df['end'] - df['start']
    df['cluster'] = df['contig'].apply(lambda x: x.split('_')[0])

    chrom_cluster_match = df.groupby(['chrom', 'cluster', 'mapq'])['length'].sum()
    chrom_cluster_match.sort_values(ascending=False, inplace=True)

    return df, chrom_cluster_match


def alignments_per_scaffold(contig_to_scaffold, aln_view, contig_view):

    # give bonus for contigs that have no breaks
    # and that are fully supported by Bionano
    select_nobreak = contig_view['contig_breaks'] == 0
    select_support = contig_view['BNG_unsupported'] == 0

    # this is currently not used
    good_contigs = set(contig_view.loc[select_nobreak & select_support, 'contig_name'])

    alignments_per_scaffold = col.defaultdict(col.Counter)

    for contig, scaffolds in contig_to_scaffold.items():
        for s in scaffolds:
            contig_align = aln_view.loc[aln_view['contig'] == contig, :].groupby(['chrom', 'mapq'])['length'].sum()
            if contig_align.empty:
                alignments_per_scaffold[s][('unaln', 0)] = 0
            for idx, sum_len in contig_align.iteritems():
                alignments_per_scaffold[s][idx] += sum_len

    # TODO do this directly in pandas DF

    # compute scaffold to chromosome assignment confidences
    # as weighted average alignment length between the two

    scaffold_chrom_match = dict()

    for scaffold, alignments in alignments_per_scaffold.items():
        # consider X, Y as single entity
        # and skip over chrUn
        count_align = col.Counter()
        for (chrom, mapq), length in alignments.items():
            if chrom == 'chrUn':
                continue
            if mapq == 0:
                continue
            elif chrom in ['chrX', 'chrY']:
                count_align['chrXY'] += length * mapq
            else:
                count_align[chrom] += length * mapq

        total_align = sum(count_align.values())
        try:
            best_match, best_align = count_align.most_common(1)[0]
        except IndexError:
            # in cases where the only recorded alignment is to
            # an unplaced chromosome, this happens
            best_match = 'random'
            confidence = 0.0
        else:
            confidence = round(best_align / total_align, 3)
        scaffold_chrom_match[scaffold] = best_match, confidence

    return scaffold_chrom_match


def classify_contig_breaks(contig_view, contig_to_scaffold, scaffold_to_chrom):

    contig_view['local_breaks'] = 0  # broken, same scaffold
    contig_view['global_breaks'] = 0  # broken, same chromosome
    contig_view['chimeric_breaks'] = 0  # broken, different chromosome
    contig_view['support_breaks'] = 0  # broken, partially unsupported by BNG
    
    # easy case: part of contig has no BNG support
    select_broken = (contig_view['BNG_supported'] > 0) & (contig_view['BNG_unsupported'] > 0)
    contig_view.loc[select_broken, 'support_breaks'] = 1

    for idx, row in contig_view.loc[contig_view['contig_breaks'] > 0, :].iterrows():
        if row['contig_breaks'] == row['support_breaks']:
            continue
        scaffolds = contig_to_scaffold[row['contig_name']]

        if len(scaffolds) == 1:
            # must be single local / within-scaffold misassembly
            contig_view.loc[idx, 'local_breaks'] += 1
            continue

        # several local breaks
        scaffold_counts = col.Counter(scaffolds)
        for name, count in scaffold_counts.most_common():
            if count < 2:
                break
            # must be local break
            contig_view.loc[idx, 'local_breaks'] += (count - 1)

        if len(set(scaffolds)) > 1:
            try:
                scaffold_chroms = [(s, scaffold_to_chrom[s]) for s in scaffolds]
            except KeyError:
                print(row)
                print(scaffolds)
                raise
            global_breaks = 0
            chimeric_breaks = 0
            local_breaks = contig_view.loc[idx, 'local_breaks']
            remaining_breaks = row['contig_breaks'] - row['support_breaks'] - local_breaks
            scored = set()
            for a, b in itertools.combinations(scaffold_chroms, 2):
                if remaining_breaks == 0:
                    # this condition is needed for cases where a single
                    # contig is scattered across several chromosomes,
                    # and we only want to count this as one chimeric break
                    break
                if (a, b) in scored or (b, a) in scored:
                    continue
                (a_scf, a_chr), (b_scf, b_chr) = a, b
                if a_scf == b_scf:
                    scored.add((a, b))
                    scored.add((b, a))
                    # local breaks covered above
                    continue
                
                if a_chr == b_chr:
                    scored.add((a, b))
                    scored.add((b, a))
                    global_breaks += 1
                    remaining_breaks -= 1
                else:
                    scored.add((a, b))
                    scored.add((b, a))
                    chimeric_breaks += 1
                    remaining_breaks -= 1

            contig_view.loc[idx, 'global_breaks'] += global_breaks
            contig_view.loc[idx, 'chimeric_breaks'] += chimeric_breaks

    # check that all breaks are accounted for
    break_counts = contig_view[
        [
            'local_breaks',
            'global_breaks',
            'chimeric_breaks',
            'support_breaks'
        ]
    ].sum(axis=1)

    mismatched = contig_view['contig_breaks'] != break_counts

    if mismatched.any():
        subset = contig_view.loc[mismatched, :]
        raise ValueError('Unaccounted contig breaks: {}'.format(subset))

    contig_view.sort_values(['BNG_supported', 'BNG_unsupported'], inplace=True, ascending=False)

    return contig_view


def assign_chrom_to_scaffolds(fasta_layout, scaffold_to_chrom):

    fasta_layout['name'] = fasta_layout['component']
    fasta_layout.loc[fasta_layout['object'] == 'scaffold', 'component'] = 'self'

    chrom_conf_columns = []
    for idx, row in fasta_layout.iterrows():
        if 'Scaffold' in row['name']:
            values = scaffold_to_chrom[row['name']]
        elif 'Scaffold' in row['object']:
            values = scaffold_to_chrom[row['object']]
        else:
            raise ValueError('{} / {}'.format(idx, row))
        chrom_conf_columns.append(values)

    add_info = pd.DataFrame(
        chrom_conf_columns,
        columns=['chrom', 'confidence'],
        index=fasta_layout.index
    )

    fasta_layout = pd.concat([fasta_layout, add_info], axis=1)

    return fasta_layout


def extract_compatible_agp_order(fasta_subset, agp_subset):

    fasta_rows = []
    names = []
    order_numbers = []
    contig_seq_starts = []
    contig_seq_ends = []
    orientation = []

    for (fasta_idx, fasta_row), (agp_idx, agp_row) in zip(fasta_subset.iterrows(), agp_subset.iterrows()):
        # two sanity checks
        if fasta_row['component'] == 'gap' and agp_row['comp_type'] != 'N':
            raise ValueError('Gap mismatch: {} / {}'.format(fasta_row, agp_row))
        if fasta_row['component'] == 'sequence' and agp_row['comp_type'] != 'W':
            raise ValueError('Contig mismatch: {} / {}'.format(fasta_row, agp_row))

        if fasta_row['component'] == 'sequence':
            agp_seq = int(agp_row['comp_end_OR_linkage'])
            fasta_seq = int(fasta_row['length'])
            if fasta_seq != agp_seq:
                raise ValueError('Seq. length mismatch: {} / {}'.format(fasta_row, agp_row))
            name = agp_row['comp_name_OR_gap_length']
            if 'subseq' in name:
                name, coords = name.split('_subseq_')
                start, end = coords.split(':')
                start = int(start) - 1  # AGP is 1-based
                end = int(end)
                assert (end - start) == fasta_row['length'], 'Length mismatch: {} / {} / {}'.format(end, fasta_row, agp_row)
            else:
                start = 0
                end = fasta_row['length']
            orient = agp_row['comp_orient_OR_linkage_evidence']
        else:
            agp_gap = int(agp_row['comp_name_OR_gap_length'])
            fasta_gap = int(fasta_row['length'])
            if fasta_gap != agp_gap:
                raise ValueError('Gap length mismatch: {} / {}'.format(fasta_row, agp_row))
            name = 'gap'
            start = -1
            end = -1
            orient = '.'
        fasta_rows.append(fasta_idx)
        order_numbers.append(float(str(agp_row['comp_number']) + '.0'))
        names.append(name)
        contig_seq_starts.append(start)
        contig_seq_ends.append(end)
        orientation.append(orient)

    return fasta_rows, names, order_numbers, contig_seq_starts, contig_seq_ends, orientation


def extract_incompatible_agp_order(fasta_subset, agp_subset):
    """
    This function exists for the Flye assemblies, which may contain
    stretches of N sequence introduced by Flye, and not by Bionano
    https://github.com/fenderglass/Flye/issues/81
    """

    fasta_rows = []
    names = []
    order_numbers = []
    contig_seq_starts = []
    contig_seq_ends = []
    orientation = []

    # more involved case, additional gaps introduced by assembler,
    # these will get order number -1, and split fasta sequences enums
    unmatched_sequences = []
    for fasta_idx, fasta_row in fasta_subset.iterrows():
        start = fasta_row['start'] + 1  # AGP is 1-based
        length = str(fasta_row['length'])  # AGP column is object type because of names
        if fasta_row['component'] == 'sequence':
            agp_column = 'comp_end_OR_linkage'
            name = 'sequence'
        else:
            agp_column = 'comp_name_OR_gap_length'
            name = 'gap'
        agp_entry = agp_subset.loc[(agp_subset['object_start'] == start) & (agp_subset[agp_column] == length), :]
        if agp_entry.empty:
            # mismatch for gap is irrelevant
            if fasta_row['component'] == 'gap':
                fasta_rows.append(fasta_idx)
                names.append('gap')
                order_numbers.append(-1.0)
                contig_seq_starts.append(-1)
                contig_seq_ends.append(-1)
                orientation.append('.')
                continue
            unmatched_sequences.append(fasta_idx)
            continue

        fasta_rows.append(fasta_idx)
        order_numbers.append(float(str(agp_entry['comp_number'].values[0]) + '.0'))
        if name == 'gap':
            names.append(name)
            contig_seq_starts.append(-1)
            contig_seq_ends.append(-1)
            orientation.append('.')
        else:
            name = agp_entry['comp_name_OR_gap_length'].values[0]
            orient = agp_entry['comp_orient_OR_linkage_evidence'].values[0]
            if 'subseq' in name:
                name, coords = name.split('_subseq_')
                start, end = coords.split(':')
                start = int(start) - 1  # AGP is 1-based
                end = int(end)
                assert (end - start) == fasta_row['length'], 'Length mismatch: {} / {} / {}'.format(end, fasta_row, agp_entry)
            else:
                start = 0
                end = fasta_row['length']
            names.append(name)
            contig_seq_starts.append(start)
            contig_seq_ends.append(end)
            orientation.append(orient)

    active_split = False
    active_count = 0
    active_split_name = ''
    active_split_orient = ''
    agp_order = None
    contig_seq_pos = -1
    last_end = -1

    for idx, fasta_row in fasta_subset.loc[unmatched_sequences, :].iterrows():
        assert fasta_row['component'] == 'sequence', 'Unmatched gap entry: {}'.format(fasta_row)
        start = fasta_row['start'] + 1
        agp_entry = agp_subset.loc[agp_subset['object_start'] == start, :]
        if agp_entry.shape[0] > 1:
            raise ValueError('Multi-start for unmatched sequence: {} / {}'.format(fasta_row, agp_entry))
        elif agp_entry.shape[0] == 1:
            # start active split
            active_split = True
            agp_order = str(agp_entry['comp_number'].values[0])
            active_count = 1
            name = agp_entry['comp_name_OR_gap_length'].values[0]
            orient = agp_entry['comp_orient_OR_linkage_evidence'].values[0]
            if 'subseq' in name:
                name, coords = name.split('_subseq_')
                start, end = coords.split(':')
                start = int(start) - 1  # AGP is 1-based
                end = start + fasta_row['length']
                assert (end - start) == fasta_row['length'], 'Length mismatch: {} / {} / {}'.format(end, fasta_row, agp_entry)
            else:
                start = 0
                end = fasta_row['length']

            contig_seq_pos = end
            last_end = fasta_row['end']
            order_number = float(agp_order + '.' + str(active_count))
            active_split_name = name
            active_split_orient = orient

            fasta_rows.append(idx)
            names.append(name)
            order_numbers.append(order_number)
            contig_seq_starts.append(start)
            contig_seq_ends.append(end)
            orientation.append(orient)
        else:
            if active_split:
                # this must always be true
                active_count += 1
                name = active_split_name
                orient = active_split_orient
                jump_gap = fasta_row['start'] - last_end
                contig_seq_pos += jump_gap
                if 'subseq' in name:
                    name, coords = name.split('_subseq_')
                start = contig_seq_pos
                end = start + fasta_row['length']
                assert (end - start) == fasta_row['length'], 'Length mismatch: {} / {} / {}'.format(end, fasta_row, agp_entry)

                contig_seq_pos = end
                last_end = fasta_row['end']
                order_number = float(agp_order + '.' + str(active_count))

                fasta_rows.append(idx)
                names.append(name)
                orientation.append(active_split_orient)
                order_numbers.append(order_number)
                contig_seq_starts.append(start)
                contig_seq_ends.append(end)

            else:
                raise ValueError('not in split mode, but zero match: {} / {}'.format(fasta_row, agp_entry))

    return fasta_rows, names, order_numbers, contig_seq_starts, contig_seq_ends, orientation


def assign_agp_order_numbers(fasta_layout, agp_layout):

    indices = []
    order_numbers = []
    names = []
    contig_seq_starts = []
    contig_seq_ends = []
    orientation = []

    for idx, scaffold_name in fasta_layout.loc[fasta_layout['object'] == 'scaffold', 'name'].items():

        fasta_subset = fasta_layout.loc[fasta_layout['object'] == scaffold_name, :]
        agp_subset = agp_layout.loc[agp_layout['object_name'] == scaffold_name, :]
        if fasta_subset.shape[0] == agp_subset.shape[0]:
            # easy case, no additional gaps, order is compatible
            subset_indices, subset_names, subset_orders, subset_starts, subset_ends, subset_orient = extract_compatible_agp_order(fasta_subset, agp_subset)
            
        else:
            # easy case, no additional gaps, order is compatible
            subset_indices, subset_names, subset_orders, subset_starts, subset_ends, subset_orient = extract_incompatible_agp_order(fasta_subset, agp_subset)

        indices.extend(subset_indices)
        names.extend(subset_names)
        order_numbers.extend(subset_orders)
        contig_seq_starts.extend(subset_starts)
        contig_seq_ends.extend(subset_ends)
        orientation.extend(subset_orient)

    fasta_layout.loc[indices, 'order'] = order_numbers
    fasta_layout.loc[indices, 'name'] = names
    fasta_layout['ctg_seq_start'] = -1
    fasta_layout['ctg_seq_end'] = -1
    fasta_layout['orientation'] = '.'
    fasta_layout.loc[indices, 'ctg_seq_start'] = contig_seq_starts
    fasta_layout.loc[indices, 'ctg_seq_end'] = contig_seq_ends
    fasta_layout.loc[indices, 'orientation'] = orientation
    orient_map = {
        '+': 1,
        '.': 0,
        '-': -1
    }
    fasta_layout['orientation'] = fasta_layout['orientation'].replace(orient_map)

    fasta_layout.loc[fasta_layout['object'] == 'scaffold', 'order'] = 0.0

    # reorder for output
    reordered = [
        'object',
        'component',
        'name',
        'order',
        'start',
        'end',
        'length',
        'orientation',
        'chrom',
        'confidence',
        'ctg_seq_start',
        'ctg_seq_end',
        'cut_sites',
        'A', 'C', 'G', 'T', 'a', 'c', 'g', 't', 'N', 'n'
    ]

    return fasta_layout[reordered]


def dump_fasta_sequences(fasta_layout, fasta_seqs, fasta_out):

    fasta_layout.loc[fasta_layout['confidence'] < 0.5, 'chrom'] = 'chrUn'

    dump_groups = fasta_layout.loc[fasta_layout['object'] == 'scaffold', ].groupby(['name', 'chrom'])['length'].sum()
    
    chroms = sorted(set(dump_groups.index.get_level_values('chrom')))

    scaffold_out = fasta_out + '.scaffolds.wg.fasta'
    with open(scaffold_out, 'w'):
        pass

    contig_seqs = fasta_layout['component'] == 'sequence'

    for c in chroms:
        chrom_contig_out = fasta_out + '.contigs.{}.fasta'.format(c)

        with open(chrom_contig_out, 'w') as dump:

            for scaffold, length in dump_groups.xs(c, level='chrom').items():
                coord = 'scf:0-{}'.format(length)
                scaffold_header = '@'.join([scaffold, c, coord])
                scaffold_seq = fasta_seqs[scaffold]
                with open(scaffold_out, 'a') as scaffold_dump:
                    write_fasta(scaffold_header, scaffold_seq, scaffold_dump)
                
                scaffold_contigs = fasta_layout['object'] == scaffold

                for idx, row in fasta_layout.loc[contig_seqs & scaffold_contigs, :].iterrows():
                    coord = 'ctg:{}-{}'.format(row['ctg_seq_start'], row['ctg_seq_end'])
                    orient = 'frw' if int(row['orientation']) == 1 else 'rev'
                    contig_name = row['name']
                    header = '@'.join([scaffold, c, str(row['order']), orient, contig_name, coord])
                    contig_seq = scaffold_seq[row['start']:row['end']]
                    write_fasta(header, contig_seq, dump)
    
    # this is just to comply with Snakemake's requirement;
    # ensure that all possible output files do exist
    possible_outputs = ['chr' + str(i) for i in range(1, 23)] + ['chrXY', 'chrUn']
    for c in possible_outputs:
        chrom_contig_out = fasta_out + '.contigs.{}.fasta'.format(c)
        if not os.path.isfile(chrom_contig_out):
            with open(chrom_contig_out, 'w') as dump:
                header = 'empty'
                sequence = 'ACGT' * 120
                write_fasta(header, sequence, dump)

    return


def write_fasta(header, sequence, output):

    line_length = 120

    chars_written = 0

    output.write('>{}\n'.format(header))

    for pos in range(len(sequence) // line_length + 1):
        start = pos * line_length
        end = start + line_length
        chars_written += output.write(sequence[start:end])
        output.write('\n')

    if not chars_written == len(sequence):
        raise ValueError('Dropped sequence during out dump: {} / {}'.format(chars_written, len(sequence)))
    _ = output.write('\n')
    return


def dump_statistics(fasta_layout, contig_view, output):

    output_layout = output + '.scaffold-layout.tsv'
    output_contigs = output + '.contig-stats.tsv'

    for df, outfile in zip([fasta_layout, contig_view], [output_layout, output_contigs]):
        df.to_csv(
            outfile,
            sep='\t',
            mode='w',
            index=False,
            header=True
        )
    return


def main():
    args = parse_args()

    out_dirs = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dirs, exist_ok=True)

    # parse (and cache) input files
    fasta_layout, fasta_seqs = load_fasta_scaffolds(args.fasta, args.output, args.no_fasta_cache)
        
    agp_layout = parse_agp_layout(args.agp)
    aln_view, chrom_cluster_match = parse_contig_alignments(args.bed)

    # compute BNG support and number of breaks (uncategorized) per contig
    contig_view, contig_to_scaffold, scaffold_to_contig = compute_bng_contig_support(agp_layout)

    scaffold_to_chrom = alignments_per_scaffold(
        contig_to_scaffold,
        aln_view, 
        contig_view,
    )

    contig_view = classify_contig_breaks(contig_view, contig_to_scaffold, scaffold_to_chrom)
    fasta_layout = assign_chrom_to_scaffolds(fasta_layout, scaffold_to_chrom)
    
    fasta_layout = assign_agp_order_numbers(fasta_layout, agp_layout)
    
    _ = dump_fasta_sequences(fasta_layout, fasta_seqs, args.output)

    _ = dump_statistics(fasta_layout, contig_view, args.output)

    return 0


if __name__ == '__main__':
    main()
