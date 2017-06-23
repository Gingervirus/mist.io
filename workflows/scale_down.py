import os
import sys
import glob
import json
import requests
import pkg_resources

from plugin import connection
from plugin.utils import LocalStorage

from cloudify.workflows import ctx as workctx
from cloudify.workflows import parameters as inputs
from cloudify.exceptions import NonRecoverableError

# FIXME
resource_package = __name__

try:
    # This path is for `cfy local` executions
    resource_path = os.path.join('../scripts', 'mega-deploy.sh')
    kubernetes_script = pkg_resources.resource_string(resource_package,
                                                      resource_path)
except IOError:
    # This path is for executions performed by Mist.io
    tmp_dir = os.path.join('/tmp/templates',
                           'mistio-kubernetes-blueprint-[A-Za-z0-9]*',
                           'scripts')
    scripts_dir = glob.glob(tmp_dir)[0]
    resource_path = os.path.join(scripts_dir, 'mega-deploy.sh')
    with open(resource_path) as f:
        kubernetes_script = f.read()


def scale_cluster_down(quantity):
    master = workctx.get_node('kube_master')
    # Get node directly from local-storage in order to have access to all of
    # its runtime_properties
    master_node = LocalStorage.get('kube_master')
    # Public IP of the Kubernetes Master used to remove nodes from the cluster
    master_ip = master_node.runtime_properties['ip']
    username = master_node.runtime_properties['auth_user']
    password = master_node.runtime_properties['auth_pass']
    # TODO deprecate this! /
    mist_client = connection.MistConnectionClient(properties=master.properties)
    cloud = mist_client.cloud
    master_machine = mist_client.machine
    # / deprecate

    worker_name = inputs.get('worker_name')
    if not worker_name:
        raise NonRecoverableError('Kubernetes Worker\'s name is missing')

    machines = cloud.machines(search=worker_name)
    if not machines:
        workctx.logger.warn('Cannot find node \'%s\'. Already removed? '
                            'Exiting...', worker_name)
        return

    workctx.logger.info('Terminating %d Kubernetes Worker(s)...', len(machines))
    counter = 0
    for m in machines:
        if not m.info['state'] in ('stopped', 'running'):
            continue
        # Properly modify the IP in order to be used in the URL
        worker_priv_ip = m.info['private_ips'][0]
        worker_selfLink = 'ip-' + str(worker_priv_ip).replace('.', '-')
        # Destroy machine
        m.destroy()
        counter += 1

        workctx.logger.info('Removing node from the Kubernetes cluster...')
        remove_node = requests.delete('https://%s:%s@%s/api/v1/nodes/%s' % \
                                      (username, password, master_ip,
                                       worker_selfLink), verify=False)
        if not remove_node.ok:
            ctx.logger.error('Failed to remove node \'%s\' from the '
                             'Kubernetes cluster', worker_selfLink)

        if counter == quantity:
            break

    workctx.logger.info('Downscaling Kubernetes cluster succeeded!')


def scale_cluster(delta):
    if isinstance(delta, basestring):
        delta = int(delta)

    if delta == 0:
        workctx.logger.info('Delta parameter equals 0! No scaling will '
                            'take place')
        return
    else:
        # TODO verify that (current number of nodes) - (delta) > 0
        delta = abs(delta)
        workctx.logger.info('Scaling Kubernetes cluster down '
                            'by %s node(s)', delta)
        scale_cluster_down(delta)


scale_cluster(inputs['delta'])

