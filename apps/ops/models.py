# ~*~ coding: utf-8 ~*~
from __future__ import unicode_literals, absolute_import

import logging
import json

from django.db import models
from django.utils.translation import ugettext_lazy as _


logger = logging.getLogger(__name__)


class Tasker(models.Model):
    uuid = models.CharField(max_length=128, verbose_name=_('UUID'), primary_key=True)
    name = models.CharField(max_length=128, blank=True, verbose_name=_('Name'))
    start = models.DateTimeField(auto_now_add=True, verbose_name=_('Start Time'))
    end = models.DateTimeField(blank=True, null=True, verbose_name=_('End Time'))
    exit_code = models.IntegerField(default=0, verbose_name=_('Exit Code'))
    completed = models.BooleanField(default=False, verbose_name=_('Is Completed'))
    hosts = models.TextField(blank=True, null=True, verbose_name=_('Hosts'))

    def __unicode__(self):
        return "%s" % self.uuid

    @property
    def total_hosts(self):
        return self.hosts.split(',')


class AnsiblePlay(models.Model):
    tasker = models.ForeignKey(Tasker, related_name='plays', blank=True, null=True)
    uuid = models.CharField(max_length=128, verbose_name=_('UUID'), primary_key=True)
    name = models.CharField(max_length=128, verbose_name=_('Name'))

    def __unicode__(self):
        return "%s<%s>" % (self.name, self.uuid)

    def to_dict(self):
        return {"uuid": self.uuid, "name": self.name}


class AnsibleTask(models.Model):
    play = models.ForeignKey(AnsiblePlay, related_name='tasks', blank=True, null=True)
    uuid = models.CharField(max_length=128, verbose_name=_('UUID'), primary_key=True)
    name = models.CharField(max_length=128, blank=True, verbose_name=_('Name'))

    def __unicode__(self):
        return "%s<%s>" % (self.name, self.uuid)

    def to_dict(self):
        return {"uuid": self.uuid, "name": self.name}

    def failed(self):
        pass

    def success(self):
        pass


class AnsibleHostResult(models.Model):
    task = models.ForeignKey(AnsibleTask, related_name='host_results', blank=True, null=True)
    name = models.CharField(max_length=128, blank=True, verbose_name=_('Name'))
    success = models.TextField(blank=True, verbose_name=_('Success'))
    skipped = models.TextField(blank=True, verbose_name=_('Skipped'))
    failed = models.TextField(blank=True, verbose_name=_('Failed'))
    unreachable = models.TextField(blank=True, verbose_name=_('Unreachable'))
    no_host = models.TextField(blank=True, verbose_name=_('NoHost'))

    def __unicode__(self):
        return "%s %s<%s>" % (self.name, str(self.is_success), self.task.uuid)

    @property
    def is_failed(self):
        if self.failed or self.unreachable or self.no_host:
            return True
        return False

    @property
    def is_success(self):
        return not self.is_failed

    @property
    def _success_data(self):
        if self.success:
            return json.loads(self.success)
        elif self.skipped:
            return json.loads(self.skipped)

    @property
    def _failed_data(self):
        if self.failed:
            return json.loads(self.failed)
        elif self.unreachable:
            return json.loads(self.unreachable)
        elif self.no_host:
            return {"msg": self.no_host}

    @property
    def failed_msg(self):
        return self._failed_data.get("msg")

    @staticmethod
    def __filter_disk(ansible_devices, exclude_devices):
        """
        过滤磁盘设备，丢弃掉不需要的设备

        :param ansible_devices: 对应的facts字段
        :param exclude_devices: <list> 一个需要被丢弃的设备,匹配规则是startwith, 比如需要丢弃sr0子类的 ['sr']
        :return: <dict> 过滤获取的结果
        """
        for start_str in exclude_devices:
            for key in ansible_devices.keys():
                if key.startswith(start_str):
                    ansible_devices.pop(key)
        return ansible_devices

    @staticmethod
    def __filter_interface(ansible_interfaces, exclude_interface):
        """
        过滤网卡设备，丢弃掉不需要的网卡， 比如lo

        :param ansible_interface: 对应的facts字段
        :param exclude_interface: <list> 一个需要被丢弃的设备,匹配规则是startwith, 比如需要丢弃lo子类的 ['lo']
        :return: <dict> 过滤获取的结果
        """
        for interface in ansible_interfaces:
            for start_str in exclude_interface:
                if interface.startswith(start_str):
                    i = ansible_interfaces.index(interface)
                    ansible_interfaces.pop(i)
        return ansible_interfaces

    @staticmethod
    def __gather_interface(facts, interfaces):
        """
        收集所有interface的具体信息

        :param facts: ansible faces
        :param interfaces: 需要收集的intreface列表
        :return: <dict> interface的详情
        """
        result = {}
        for key in interfaces:
            if "ansible_" + key in facts.keys():
                result[key] = facts.get(key)
        return result

    def __deal_setup(self):
        """
        处理ansible setup模块收集到的数据，提取资产需要的部分

        :return: <dict> {"msg": <str>, "data": <dict>}, 注意msg是异常信息, 有msg时 data为None
        """
        result = self._success_data
        module_name = result['invocation'].get('module_name') if result.get('invocation') else None
        if module_name is not None:
            if module_name != "setup":
                return {"msg": "the property only for ansible setup module result!, can't support other module", "data":None}
            else:
                data = {}
                facts =result.get('ansible_facts')
                interfaces = self.__filter_interface(facts.get('ansible_interfaces'), ['lo'])

                cpu_describe = "%s %s" % (facts.get('ansible_processor')[0], facts.get('ansible_processor')[1]) if len(facts.get('ansible_processor')) >= 2 else ""

                data['sn'] = facts.get('ansible_product_serial')
                data['env'] = facts.get('ansible_env')
                data['os'] = "%s %s(%s)" % (facts.get('ansible_distribution'),
                                         facts.get('ansible_distribution_version'),
                                         facts.get('ansible_distribution_release'))
                data['mem'] = facts.get('ansible_memtotal_mb')
                data['cpu'] = "%s %d核" % (cpu_describe, facts.get('ansible_processor_count'))
                data['disk'] = self.__filter_disk(facts.get('ansible_devices'), ['sr'])
                data['interface'] = self.__gather_interface(facts, interfaces)
                return {"msg": None, "data": data}
        else:
            return {"msg": "there isn't module_name field! can't process this data format", "data": None}

    @property
    def deal_setup(self):
        try:
            return self.__deal_setup()
        except Exception as e:
            return {"msg": "deal with setup data failed, %s" % e.message, "data": None}

    def __deal_ping(self):
        """
        处理ansible ping模块收集到的数据

        :return: <dict> {"msg": <str>, "data": {"success": <bool>}}, 注意msg是异常信息, 有msg时 data为None
        """
        result = self._success_data
        module_name = result['invocation'].get('module_name') if result.get('invocation') else None
        if module_name is not None:
            if module_name != "ping":
                return {"msg": "the property only for ansible setup module result!, can't support other module", "data":None}
            else:
                ping = True if result.get('ping') == "pong" else False

                return {"msg": None, "data": {"success": ping}}
        else:
            return {"msg": "there isn't module_name field! can't process this data format", "data": None}

    @property
    def deal_ping(self):
        try:
            return self.__deal_ping()
        except Exception as e:
            return {"msg": "deal with ping data failed, %s" % e.message, "data": None}


