#!/bin/bash
source novarc
for i in router port subnet net; do
for x in `neutron $i-list | awk '{ print $2 }'`; do neutron $i-delete $x ; done
done
