# monitor whether a task of "srun bash" has been assigned to gpu

import os
import time
import subprocess
from copy import deepcopy
import pandas as pd
import hydra
from omegaconf import DictConfig, OmegaConf
from rich import print as rprint
from rich.syntax import Syntax

from utils import send_email


attri_name = ["jobid", "partition", "name", "user", "status", "time", "nodes", "nodelist(reason)", "features"]

def get_cur_tasks():
    result = subprocess.run(["squeue | grep $USER"], capture_output=True, text=True, shell=True)
    cur_tasks = {}
    tasks = result.stdout.split("\n")
    for task in tasks:
        attri = task.split()
        if len(attri) > 0:
            cur_task = {}
            for i in range(len(attri)):
                cur_task[attri_name[i]] = attri[i]
            cur_tasks[cur_task['jobid']] = cur_task
    return cur_tasks

def tasks_changed(last_tasks, cur_tasks, ignore_keys=['time']):
    if len(last_tasks) != len(cur_tasks):
        return True
    
    if last_tasks == cur_tasks:
        return False
    else:
        for jobid in cur_tasks:
            if jobid not in last_tasks:
                return True
            else:
                for key in attri_name:
                    if key not in ignore_keys:
                        if last_tasks[jobid][key] != cur_tasks[jobid][key]:
                            return True
        return False

def awesome_print(tasks):
    tasks_list = []
    for jobid in tasks:
        tasks_list.append(tasks[jobid])
    df = pd.DataFrame(tasks_list)
    print(df)
    return df.to_string()

@hydra.main(version_base=None, config_path="../config", config_name="config.yaml")
def monitor_srun_bash(cfg):
    last_tasks = get_cur_tasks()
    print_every = 30
    print_cnt = 0
    sleep_time = 5
    print('Time: ', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))

    while True:
        time.sleep(sleep_time)
        cur_tasks = get_cur_tasks()

        print_cnt += sleep_time
        if print_cnt >= print_every:
            print('Time: ', time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
            print_cnt = 0

        if tasks_changed(last_tasks, cur_tasks):
            # print with color
            print("\033[91m" + "tasks changed" + "\033[0m")
            last_tasks_str = awesome_print(last_tasks)
            cur_tasks_str = awesome_print(cur_tasks)
            last_tasks = cur_tasks
            subject = "Slurm Tasks Changed"
            content = "Tasks changed\n\n"
            content += "Last tasks:\n\n"
            content += last_tasks_str
            content += "\n\nCurrent tasks:\n\n"
            content += cur_tasks_str
            to_email = cfg.email.to_email
            send_email.send_email(cfg, subject, content, to_email)

if __name__ == "__main__":
    monitor_srun_bash()
    
