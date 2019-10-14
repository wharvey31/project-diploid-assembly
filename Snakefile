
include: 'smk_include/handle_data_download.smk'
include: 'smk_include/results_child.smk'
include: 'smk_include/results_parents.smk'

localrules: master

rule master:
    input:
        # this triggers a checkpoint
        # for downloading the strand-seq data
        expand('input/fastq/strand-seq/{individual}_{bioproject}/requests',
                individual=['HG00733', 'HG00732', 'HG00731'],
                bioproject=['PRJEB12849']),
        rules.master_results_child.input,
        rules.master_results_parents.input


    message: 'Executing ALL'


def make_log_useful(log_path, status):

    my_env = dict(os.environ)
    with open(log_path, 'a') as logfile:
        _ = logfile.write('\n===[{}]===\n'.format(status))
        _ = logfile.write('Host: {}\n'.format(my_env.get('HOSTNAME', 'N/A')))
        _ = logfile.write('Display: {}\n'.format(my_env.get('DISPLAY', 'N/A')))
        _ = logfile.write('Shell: {}\n'.format(my_env.get('SHELL', 'N/A')))
        _ = logfile.write('Terminal: {}\n'.format(my_env.get('TERM', 'N/A')))
        _ = logfile.write('Screen: {}\n'.format(my_env.get('STY', 'N/A')))
        _ = logfile.write('Conda ENV: {}\n'.format(my_env.get('CONDA_DEFAULT_ENV', 'N/A')))
        _ = logfile.write('\n')
    return


onsuccess:
    make_log_useful(log, 'SUCCESS')
    import socket
    host = socket.gethostname()
    if config['notify'] and 'bibigrid' not in host:
        shell('mail -s "[Snakemake] DGA - SUCCESS" {} < {{log}}'.format(config['notify_email']))


onerror:
    make_log_useful(log, 'ERROR')
    import socket
    host = socket.gethostname()
    if config['notify'] and 'bibigrid' not in host:
        shell('mail -s "[Snakemake] DGA - ERRROR" {} < {{log}}'.format(config['notify_email']))
