# -*- coding: utf-8 -*-

"""
Created on 15.01.20
"""
import os
import stat
import shutil
from argparse import ArgumentParser, Namespace
from typing import List, Dict

from scripts.classes import CaseSensitiveConfigParser
from scripts.classes.intelmqbot import IntelMQBot
from scripts.classes.intelmqdetails import IntelMQDetails
from scripts.classes.pipelinedetail import PipelineDetail
from scripts.libs.abstractbase import AbstractBaseTool
from scripts.libs.exceptions import IncorrectArgumentException, ToolException
from scripts.libs.utils import pretty_json

__author__ = 'Weber Jean-Paul'
__email__ = 'jean-paul.weber@restena.lu'
__copyright__ = 'Copyright 2019-present, Restena CSIRT'
__license__ = 'GPL v3+'


class Installer(AbstractBaseTool):
    BASE = '/home/jpweber/workspace/intelmq-bots/fake_install'

    def get_arg_parser(self) -> ArgumentParser:
        arg_parse = ArgumentParser(prog='install', description='Tool for installing bots')
        arg_parse.add_argument('-i', '--install', default=None,
                               help='Path of the bot to install (Note: Module Folder only)', type=str)
        arg_parse.add_argument('-u', '--uninstall', default=None,
                               help='Path of the bot to uninstall (Note: Module Folder only)', type=str)
        self.set_default_arguments(arg_parse)
        return arg_parse

    def start(self, args: Namespace) -> None:

        if args.install or args.uninstall:
            if args.install:
                bot_path = args.install
                removal = False
            else:
                bot_path = args.uninstall
                removal = True

            intelmq_details, custom_bots = self.get_custom_bots(args.dev)
            not_found = True
            for custom_bot in custom_bots:
                if bot_path in custom_bot.code_file:
                    not_found = False
                    break

            if not_found:
                print('Bot with file {} cannot be found'.format(bot_path))
            else:
                mode_ = 'insert'
                if removal:
                    mode_ = 'remove'
                    pipeline_map = self.check_pipeline(intelmq_details, [custom_bot])
                    self.remove_runtime(intelmq_details, pipeline_map, [custom_bot])

                self.update_bots_file([custom_bot], mode_, args.dev)
                self.update_entry_points(intelmq_details, [custom_bot], mode_)
                self.update_executable(intelmq_details, [custom_bot], mode_)
                self.update_files(intelmq_details, [custom_bot], mode_)
                print('BOT Class {} was successfully {}ed'.format(custom_bot.class_name, mode_))
        else:
            raise IncorrectArgumentException()

    def get_version(self) -> str:
        return '0.1'

    @staticmethod
    def update_entry_points(intelmq_details: IntelMQDetails, bots: List[IntelMQBot], mode_: str) -> None:
        config = CaseSensitiveConfigParser()
        with open(intelmq_details.entry_point_location, 'r') as f:
            configuration = f.read()
        for bot in bots:
            botline = '{} = {}\n'.format(bot.code_module, bot.entry_point)
            if botline in configuration and mode_ == 'remove':
                print('Removed {} from entry_points.txt'.format(botline))
                configuration = configuration.replace(botline, '')
            else:
                if mode_ == 'install':
                    print('Added {} to entry_points.txt'.format(botline))
                    configuration = '{}{}'.format(configuration, botline)
        with open(intelmq_details.entry_point_location, 'w') as f:
            f.write(configuration)

    def update_executable(self, intelmq_details: IntelMQDetails, bots: List[IntelMQBot], mode_: str) -> None:
        if os.path.exists(intelmq_details.bin_folder):
            for bot in bots:
                file_path = os.path.join(intelmq_details.bin_folder, bot.code_module)
                if mode_ == 'remove':
                    if os.path.exists(file_path):
                        print('Removed file {}'.format(file_path))
                        os.remove(file_path)
                elif mode_ == 'insert':
                    text = "#!/bin/python3.6\n" \
                           "# EASY-INSTALL-ENTRY-SCRIPT: 'intelmq==2.1.1','console_scripts','{0}'" \
                           "__requires__ = 'intelmq==2.1.1'\n" \
                           "import re\n" \
                           "import sys\n" \
                           "from pkg_resources import load_entry_point\n\nif __name__ == '__main__':\n" \
                           "    sys.argv[0] = re.sub(r'(-script\.pyw?|\.exe)?$', '', sys.argv[0])\n" \
                           "    sys.exit(\n" \
                           "load_entry_point('intelmq==2.1.1', 'console_scripts', '{0}')()\n" \
                           ")".format(bot.code_module)
                    with open(file_path, 'w+') as f:
                        f.write(text)
                    #Note: must be in octal (771_8 = 457_10)
                    os.chmod(file_path, 493)
                    print('File {} created'.format(file_path))
        else:
            raise ToolException('Path {} for executables does not exist'.format(intelmq_details.bin_folder))

    def build_pipeline_map(self, pipeline: dict) -> Dict[str, PipelineDetail]:
        seen_details = dict()
        for instance_name, details in pipeline.items():
            if instance_name not in seen_details.keys():
                seen_details[instance_name] = PipelineDetail()
                seen_details[instance_name].bot_instance_name = instance_name
        for instance_name, details in pipeline.items():
            source_queue = details.get('source-queue')
            if source_queue:
                queue_instance_name = source_queue.replace('-queue', '')
                if queue_instance_name in seen_details.keys():
                    seen_details[queue_instance_name].source = seen_details[queue_instance_name]

            for destination in details.get('destination-queues', []):
                queue_instance_name = destination.replace('-queue', '')
                if queue_instance_name in seen_details.keys():
                    seen_details[instance_name].destinations.append(seen_details[queue_instance_name])
        return seen_details

    def check_pipeline(self, intelmq_details: IntelMQDetails, bots: List[IntelMQBot]) -> Dict[str, PipelineDetail]:
        pipeline = self.get_config(intelmq_details.pipeline_file)
        pipeline_map = self.build_pipeline_map(pipeline)
        for bot in bots:
            for instance in bot.instances:
                item = pipeline_map.get(instance.name)
                if item:
                    if item.destinations or item.source:
                        # the bot is still used!
                        raise ToolException('The Bot {} is still used in the pipes. Remove pipes first.'.format(bot.code_file))
        return pipeline_map

    def remove_runtime(self, intelmq_details: IntelMQDetails, pipeline_map: Dict[str, PipelineDetail],
                       bots: List[IntelMQBot]) -> None:
        runtime = self.get_config(intelmq_details.runtime_file)
        for bot in bots:
            can_be_removed = True
            if bot.instances:
                for instance in bot.instances:
                    item = pipeline_map.get(instance.name)
                    if item:
                        can_be_removed = False
                    if can_be_removed:
                        del runtime[instance.name]
        with open(intelmq_details.runtime_file, 'w') as f:
            f.write(pretty_json(runtime))

    def update_files(self, intelmq_details: IntelMQDetails, bots: List[IntelMQBot], mode_: str) -> None:
        for bot in bots:
            directory_to_from = os.path.dirname(bot.code_file)
            bot_directory = directory_to_from.replace(self.bot_location, '')
            directory_to_to = '{}{}'.format(intelmq_details.bot_folder, bot_directory)
            if os.path.exists(directory_to_from):
                if mode_ == 'remove':
                    if os.path.exists(directory_to_to):
                        shutil.rmtree(directory_to_to)
                        print('Directory {} was removed'.format(directory_to_to))
                    else:
                        raise ToolException('Directory {} does not exit'.format(directory_to_to))
                elif mode_ == 'insert':
                    if os.path.exists(directory_to_to):
                        print('Directory {} already exists skipping'.format(directory_to_to))
                    else:
                        shutil.copytree(directory_to_from, directory_to_to)
                        print('Directory {} was created'.format(directory_to_to))
            else:
                raise ToolException('Directory {} does not exit'.format(directory_to_from))
