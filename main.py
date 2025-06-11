#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Minecraft Server Manager (Python Edition)

This script is a Python conversion of the Java-based Minecraft Server Manager.
It provides a command-line interface to download, install, and manage
Vanilla, Forge, Fabric, and NeoForge Minecraft servers on Linux systems.

Author: HappyDIY
Conversion Date: 2025-06-11
Original Java Author: Unknown
Version: 1.2 - Fixed subprocess call in JavaFinder
"""

import os
import sys
import json
import re
import shlex
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Set, Any

# Third-party library dependency. Install with: pip install requests
try:
    import requests
except ImportError:
    print("错误：'requests' 库未安装。请使用 'pip install requests' 命令进行安装。")
    sys.exit(1)


# ==============================================================================
# 1. API 客户端模块 (Mojang, Forge, Fabric, NeoForge)
# ==============================================================================

class AnsiColors:
    """终端输出的 ANSI 颜色代码"""
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"


class ApiClients:
    """用于与各种 Minecraft 相关 API 通信的客户端"""
    _CLIENT = requests.Session()
    _CLIENT.headers.update({'User-Agent': 'MinecraftServerManager/1.2 (Python)'})
    _OBJECT_MAPPER = json

    # --- Mojang API ---
    @dataclass
    class MinecraftVersion:
        id: str
        type: str
        url: str

    @staticmethod
    def get_minecraft_versions(filter_type: str = "release") -> List[MinecraftVersion]:
        """从 Mojang API 获取 Minecraft 版本列表"""
        url = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
        versions = []
        try:
            response = ApiClients._CLIENT.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            for v_data in data.get("versions", []):
                if not filter_type or v_data.get("type") == filter_type:
                    versions.append(ApiClients.MinecraftVersion(
                        id=v_data['id'], type=v_data['type'], url=v_data['url']
                    ))
            return versions
        except requests.RequestException as e:
            print(f"\n{AnsiColors.RED}错误：获取 Minecraft 版本失败: {e}{AnsiColors.RESET}")
            return []

    @staticmethod
    def get_minecraft_download_url(version_id: str) -> Optional[str]:
        """获取特定 Minecraft 版本的服务端下载链接"""
        all_versions = ApiClients.get_minecraft_versions(filter_type="")
        version_url = next((v.url for v in all_versions if v.id == version_id), None)
        if not version_url:
            return None
        try:
            response = ApiClients._CLIENT.get(version_url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("downloads", {}).get("server", {}).get("url")
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"\n{AnsiColors.RED}错误：获取 {version_id} 的下载链接失败: {e}{AnsiColors.RESET}")
            return None

    # --- Forge API ---
    @dataclass
    class ForgeVersion:
        full_version: str
        mc_version: str
        forge_version: str

        def get_installer_url(self) -> str:
            return f"https://maven.minecraftforge.net/net/minecraftforge/forge/{self.full_version}/forge-{self.full_version}-installer.jar"

    @staticmethod
    def get_forge_versions(mc_version: str) -> List[ForgeVersion]:
        """从 Forge Maven 获取指定 Minecraft 版本的 Forge 版本列表"""
        url = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
        versions = []
        try:
            response = ApiClients._CLIENT.get(url, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for version_node in root.findall(".//version"):
                full_version = version_node.text
                if full_version and full_version.startswith(f"{mc_version}-"):
                    parts = full_version.split('-', 1)
                    if len(parts) == 2:
                        versions.append(ApiClients.ForgeVersion(
                            full_version=full_version,
                            mc_version=parts[0],
                            forge_version=parts[1]
                        ))
            return sorted(versions, key=lambda v: v.forge_version, reverse=True)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"\n{AnsiColors.RED}错误：获取 Forge 版本失败: {e}{AnsiColors.RESET}")
            return []

    # --- Fabric API ---
    @dataclass
    class FabricLoaderVersion:
        version: str
        stable: bool

    @staticmethod
    def get_fabric_loader_versions(mc_version: str) -> List[FabricLoaderVersion]:
        """从 Fabric Meta API 获取 Fabric 加载器版本"""
        url = f"https://meta.fabricmc.net/v2/versions/loader/{mc_version}"
        versions = []
        try:
            response = ApiClients._CLIENT.get(url, timeout=10)
            if response.status_code == 404: # 无版本可用
                 return []
            response.raise_for_status()
            data = response.json()
            for item in data:
                loader_data = item.get("loader")
                if loader_data:
                    versions.append(ApiClients.FabricLoaderVersion(
                        version=loader_data['version'],
                        stable=loader_data['stable']
                    ))
            return versions
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"\n{AnsiColors.RED}错误：获取 Fabric 加载器版本失败: {e}{AnsiColors.RESET}")
            return []

    @staticmethod
    def get_fabric_installer_url() -> Optional[str]:
        """获取最新的 Fabric 安装程序下载链接"""
        url = "https://meta.fabricmc.net/v2/versions/installer"
        try:
            response = ApiClients._CLIENT.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data and isinstance(data, list):
                return data[0].get("url")
            return None
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"\n{AnsiColors.RED}错误：获取 Fabric 安装程序链接失败: {e}{AnsiColors.RESET}")
            return None

    # --- NeoForge API ---
    @dataclass
    class NeoForgeVersion:
        full_version: str
        mc_version: str
        neoforge_version: str

        def get_installer_url(self) -> str:
            return f"https://maven.neoforged.net/releases/net/neoforged/neoforge/{self.full_version}/neoforge-{self.full_version}-installer.jar"

    @staticmethod
    def get_neoforge_versions(mc_version: str) -> List[NeoForgeVersion]:
        """从 NeoForged Maven 获取 NeoForge 版本列表"""
        mc_parts = mc_version.split('.')
        if len(mc_parts) < 2:
            return []
        mc_major_prefix = mc_parts[1]
        
        url = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
        versions = []
        try:
            response = ApiClients._CLIENT.get(url, timeout=15)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for version_node in root.findall(".//version"):
                full_version = version_node.text
                if full_version and full_version.startswith(f"{mc_major_prefix}."):
                    versions.append(ApiClients.NeoForgeVersion(
                        full_version=full_version,
                        mc_version=f"1.{full_version.split('.')[0]}",
                        neoforge_version=full_version
                    ))
            return sorted(versions, key=lambda v: v.full_version, reverse=True)
        except (requests.RequestException, ET.ParseError) as e:
            print(f"\n{AnsiColors.RED}错误：获取 NeoForge 版本失败: {e}{AnsiColors.RESET}")
            return []


# ==============================================================================
# 2. Java 查找器模块
# ==============================================================================
@dataclass
class JavaInstallation:
    """描述一个找到的 Java 安装"""
    java_home: Path
    java_type: str  # JDK 或 JRE
    version: str
    vendor: str
    major_version: int
    display_alias: str
    path_depth: int

    def __str__(self):
        return f"{self.display_alias} - {self.java_home} (v: {self.version}, {self.vendor})"


class JavaFinder:
    """在系统上定位 Java 安装"""

    @staticmethod
    def get_java_details(java_exe: Path) -> Optional[JavaInstallation]:
        """运行 'java -version' 并解析其输出"""
        try:
            # *** FIX v1.2: Corrected subprocess call to avoid ValueError ***
            # Removed capture_output=True as it conflicts with stderr argument.
            # Explicitly capture stdout and redirect stderr to stdout.
            result = subprocess.run(
                [str(java_exe), "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5
            )
            output = result.stdout

            # 解析版本
            version_match = re.search(r'version "([^"]+)"', output)
            if not version_match:
                version_match = re.search(r'(?:openjdk|java) version "([^"]+)"', output, re.IGNORECASE)
                if not version_match: return None
            
            version_str = version_match.group(1)
            
            # 解析主版本号
            major_version = 0
            version_parts = re.findall(r'\d+', version_str)
            if version_parts:
                if version_parts[0] == '1':
                    major_version = int(version_parts[1]) if len(version_parts) > 1 else 8
                else:
                    major_version = int(version_parts[0])

            # 解析供应商
            vendor = "Unknown"
            output_lower = output.lower()
            if "zulu" in output_lower: vendor = "Zulu"
            elif "temurin" in output_lower: vendor = "Eclipse Temurin"
            elif "graalvm" in output_lower: vendor = "GraalVM"
            elif "oracle corporation" in output_lower or ("java(tm) se" in output_lower and "openjdk" not in output_lower): vendor = "Oracle"
            elif "openjdk" in output_lower: vendor = "OpenJDK"
            
            # 使用真实路径来确定 JAVA_HOME
            real_java_exe = java_exe.resolve()
            java_home = real_java_exe.parent.parent
            java_type = "JDK" if (java_home / "bin" / "javac").exists() else "JRE"
            
            sanitized_vendor = vendor.lower().replace(" ", "").replace("eclipse", "")
            display_alias = f"{sanitized_vendor}{major_version}"
            
            return JavaInstallation(
                java_home=java_home,
                java_type=java_type,
                version=version_str,
                vendor=vendor,
                major_version=major_version,
                display_alias=display_alias,
                path_depth=len(real_java_exe.parts)
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

    @staticmethod
    def find_java_installations(search_paths: List[str]) -> List[JavaInstallation]:
        """在系统上定位 Java 安装 (已改进)"""
        found_installations: Dict[str, JavaInstallation] = {}
        processed_homes: Set[Path] = set()
        candidate_exes: Set[Path] = set()

        # 1. 从 JAVA_HOME 和系统 PATH 开始
        if java_home_env := os.environ.get('JAVA_HOME'):
            candidate_exes.add(Path(java_home_env) / 'bin' / 'java')
        
        if java_in_path := shutil.which('java'):
            candidate_exes.add(Path(java_in_path))

        # 2. 遍历通用目录进行搜索
        for search_path_str in search_paths:
            search_path = Path(search_path_str).expanduser()
            if not search_path.is_dir():
                continue
            for java_exe in search_path.rglob('bin/java'):
                if os.access(java_exe, os.X_OK):
                    candidate_exes.add(java_exe)

        # 3. 处理所有找到的唯一候选
        for java_exe_candidate in candidate_exes:
            try:
                if not java_exe_candidate.is_file() or not os.access(java_exe_candidate, os.X_OK):
                    continue

                details = JavaFinder.get_java_details(java_exe_candidate)
                if details:
                    if details.java_home in processed_homes:
                        continue
                    processed_homes.add(details.java_home)
                    
                    if details.display_alias not in found_installations or \
                       details.path_depth < found_installations[details.display_alias].path_depth:
                        found_installations[details.display_alias] = details
            except (OSError):
                continue

        # 按主版本号降序排序
        return sorted(list(found_installations.values()), key=lambda x: x.major_version, reverse=True)

# ==============================================================================
# 3. 工具/UI 模块
# ==============================================================================
class Utils:
    """提供用户界面和通用功能的辅助类"""

    @staticmethod
    def colorize(text: str, color: str) -> str:
        """为文本添加颜色"""
        return f"{color}{text}{AnsiColors.RESET}"
    
    @staticmethod
    def print_color(message: str, color: str):
        """打印带颜色的消息"""
        print(Utils.colorize(message, color))

    @staticmethod
    def print_on_same_line(message: str):
        """在同一行打印消息，覆盖之前的内容"""
        sys.stdout.write(f"\r\033[K{message}")
        sys.stdout.flush()

    @staticmethod
    def clear_line():
        """清除当前行"""
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    @staticmethod
    def show_menu(title: str, prompt: str, items: List[str]) -> int:
        """显示一个菜单并返回用户的选择（基于1的索引）"""
        if not items:
            return -1
        print(f"\n{Utils.colorize(f'====== {title} ======', AnsiColors.YELLOW)}")
        for i, item in enumerate(items, 1):
            print(Utils.colorize(f"{i}. {item}", AnsiColors.CYAN))
        print(Utils.colorize("----------------------------------------", AnsiColors.YELLOW))
        
        while True:
            try:
                choice_str = input(f"{Utils.colorize(prompt, AnsiColors.GREEN)} (输入 'q' 退出): ").strip().lower()
                if choice_str in ['q', 'quit']:
                    return -1
                choice = int(choice_str)
                if 1 <= choice <= len(items):
                    return choice
                else:
                    Utils.print_color("无效的序号，请重新输入。", AnsiColors.RED)
            except ValueError:
                Utils.print_color("请输入数字或 'q' 退出。", AnsiColors.RED)

    @staticmethod
    def prompt_yes_no(question: str) -> Optional[bool]:
        """向用户提出一个 Y/N 问题"""
        prompt = f"{Utils.colorize(question, AnsiColors.GREEN)} (y/n/q): "
        while True:
            choice = input(prompt).strip().lower()
            if choice == 'y':
                return True
            elif choice == 'n':
                return False
            elif choice in ['q', 'quit']:
                Utils.print_color("用户取消操作。", AnsiColors.YELLOW)
                return None
            else:
                Utils.print_color("无效输入，请输入 'y', 'n' 或 'q'。", AnsiColors.RED)

# ==============================================================================
# 4. 主程序逻辑模块
# ==============================================================================
class ServerType(Enum):
    VANILLA = auto()
    FORGE = auto()
    FABRIC = auto()
    NEOFORGE = auto()

class MinecraftManager:
    """主应用程序类，包含所有业务逻辑"""
    MINECRAFT_SERVER_BASE_DIR = Path("minecraft_server")

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=5)

    def run(self):
        """脚本主入口点"""
        try:
            Utils.print_color("====== Minecraft 服务器管理脚本 (Python 版) ======", AnsiColors.YELLOW)
            self._check_os()
            
            # 并行查找 Java
            java_search_future = self.executor.submit(self._find_and_sort_java)
            
            installed_versions = self._get_installed_versions()
            server_to_start: Optional[Path] = None
            java_path_for_start: Optional[str] = None

            install_new = False
            if not installed_versions:
                install_new = True
            else:
                choice = Utils.show_menu("选择操作", "请选择要执行的操作：", ["启动已有服务器", "安装新服务器"])
                if choice == 2:
                    install_new = True
                elif choice == -1: # 用户退出
                    install_new = False
                    Utils.print_color("操作已取消。", AnsiColors.YELLOW)


            if install_new:
                install_result = self._install_new_server(java_search_future)
                if install_result:
                    start_now_prompt = Utils.prompt_yes_no("安装完成。是否立即启动服务器?")
                    if start_now_prompt:
                        server_to_start, java_path_for_start = install_result
                    elif start_now_prompt is None: # 用户选择退出
                         pass
                    else:
                        Utils.print_color("您可以稍后再次运行脚本以启动服务器。", AnsiColors.GREEN)
                else:
                    Utils.print_color("安装已取消。", AnsiColors.YELLOW)
            elif installed_versions:
                server_to_start = self._select_existing_server(installed_versions)

            if server_to_start:
                dir_name = server_to_start.name
                stype = self._infer_server_type(dir_name)
                mc_version = self._infer_mc_version(dir_name)
                
                Utils.print_color(f"\n准备启动服务器: {dir_name}", AnsiColors.CYAN)

                if not java_path_for_start:
                    java_path_for_start = self._select_java_for_version(mc_version, server_to_start, java_search_future)

                if java_path_for_start:
                    self._start_server(stype, java_path_for_start, server_to_start)
                else:
                    Utils.print_color("未选择 Java，启动中止。", AnsiColors.RED)

        except (KeyboardInterrupt):
            Utils.print_color("\n\n操作被用户中断。正在退出...", AnsiColors.YELLOW)
        except Exception as e:
            Utils.print_color(f"\n程序运行出现严重错误: {e}", AnsiColors.RED)
            import traceback
            traceback.print_exc()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
            Utils.print_color("\n脚本执行完毕。", AnsiColors.GREEN)
            sys.exit(0)
    
    def _check_os(self):
        if not sys.platform.startswith("linux"):
            Utils.print_color("错误：本脚本仅设计用于 Linux 系统。", AnsiColors.RED)
            sys.exit(1)

    def _find_and_sort_java(self) -> List[JavaInstallation]:
        Utils.print_on_same_line("正在搜索 Java 环境...")
        common_paths = ["/usr/lib/jvm", os.path.expanduser("~/.sdkman/candidates/java"), "/opt"]
        installations = JavaFinder.find_java_installations(common_paths)
        Utils.clear_line()
        return installations

    def _get_installed_versions(self) -> Dict[str, Path]:
        """获取已安装的服务器版本"""
        installed = {}
        if not self.MINECRAFT_SERVER_BASE_DIR.is_dir():
            return {}
        for version_dir in self.MINECRAFT_SERVER_BASE_DIR.iterdir():
            if version_dir.is_dir() and (version_dir / "eula.txt").exists():
                installed[version_dir.name] = version_dir
        
        return dict(sorted(installed.items(), key=lambda item: item[0], reverse=True))

    def _select_existing_server(self, installed_versions: Dict[str, Path]) -> Optional[Path]:
        Utils.print_color("\n--- 启动已有服务器 ---", f"{AnsiColors.BOLD}{AnsiColors.YELLOW}")
        version_ids = list(installed_versions.keys())
        choice = Utils.show_menu("选择要启动的 Minecraft 服务器", "请选择已安装的服务器：", version_ids)
        if choice == -1:
            return None
        return installed_versions[version_ids[choice - 1]]
        
    def _install_new_server(self, java_future) -> Optional[Tuple[Path, str]]:
        """处理新服务器的安装流程"""
        Utils.print_color("\n--- 开始新服务器安装流程 ---", f"{AnsiColors.BOLD}{AnsiColors.YELLOW}")
        
        # 1. 选择 MC 版本
        mc_version = self._prompt_for_mc_version()
        if not mc_version: return None
        
        # 2. 并行获取 ModLoader 版本，同时选择服务器类型
        forge_future = self.executor.submit(ApiClients.get_forge_versions, mc_version)
        fabric_future = self.executor.submit(ApiClients.get_fabric_loader_versions, mc_version)
        neoforge_future = self.executor.submit(ApiClients.get_neoforge_versions, mc_version)
        
        server_type = self._prompt_for_server_type(mc_version, forge_future, fabric_future, neoforge_future)
        if not server_type: return None

        # 3. 选择 ModLoader 版本
        mod_version: Optional[str] = None
        if server_type != ServerType.VANILLA:
            forge_versions = forge_future.result()
            fabric_versions = fabric_future.result()
            neoforge_versions = neoforge_future.result()
            mod_version = self._prompt_for_mod_loader_version(server_type, forge_versions, fabric_versions, neoforge_versions)
            if not mod_version: return None
            
        # 4. 确定目录名和路径
        server_dir_name = self._get_server_dir_name(server_type, mc_version, mod_version)
        server_dir = self.MINECRAFT_SERVER_BASE_DIR / server_dir_name
        
        if server_dir.exists():
            overwrite = Utils.prompt_yes_no(f"目录 {server_dir_name} 已存在。是否覆盖安装?")
            if overwrite is None or not overwrite: # 取消或选择否
                return None
            if overwrite:
                 shutil.rmtree(server_dir)

        # 5. 选择 Java
        java_path = self._select_java_for_version(mc_version, server_dir, java_future)
        if not java_path: return None

        # 6. 执行安装
        self._install_server_core(server_type, mc_version, mod_version, server_dir, java_path)
        self._accept_eula(server_dir / "eula.txt")
        
        return server_dir, java_path

    def _prompt_for_mc_version(self) -> Optional[str]:
        """提示用户选择 Minecraft 版本"""
        all_versions = ApiClients.get_minecraft_versions("release")
        if not all_versions:
            raise IOError("未能获取到 Minecraft 版本列表。")
            
        def get_major_minor(v_id):
            parts = v_id.split('.')
            return f"{parts[0]}.{parts[1]}" if len(parts) > 1 else v_id
        
        major_series: List[str] = sorted(list(set(get_major_minor(v.id) for v in all_versions)), reverse=True)
        
        choice = Utils.show_menu("选择 Minecraft 主要版本系列", "请选择版本系列：", major_series)
        if choice == -1: return None
        selected_series = major_series[choice - 1]
        
        specific_versions = [v.id for v in all_versions if get_major_minor(v.id) == selected_series]
        
        choice = Utils.show_menu("选择 Minecraft 具体版本", "请选择服务端版本：", specific_versions)
        if choice == -1: return None
        
        return specific_versions[choice - 1]

    def _prompt_for_server_type(self, mc_version, forge_future, fabric_future, neoforge_future) -> Optional[ServerType]:
        """提示用户选择服务端类型"""
        futures = {"Forge": forge_future, "Fabric": fabric_future, "NeoForge": neoforge_future}
        while not all(f.done() for f in futures.values()):
            status_parts = []
            for name, f in futures.items():
                icon = Utils.colorize("[✓]", AnsiColors.GREEN) if f.done() else Utils.colorize("[..]", AnsiColors.YELLOW)
                status_parts.append(f"{icon} {name}")
            Utils.print_on_same_line("正在获取服务端信息: " + " ".join(status_parts))
            time.sleep(0.1)
        Utils.clear_line()

        availability = {
            ServerType.VANILLA: True,
            ServerType.FORGE: bool(forge_future.result()),
            ServerType.FABRIC: bool(fabric_future.result()),
            ServerType.NEOFORGE: bool(neoforge_future.result()),
        }

        available_types = [stype for stype, is_avail in availability.items() if is_avail]
        available_display = [stype.name for stype in available_types]

        if len(available_types) == 1:
            Utils.print_color(f"只找到 {available_types[0].name} 服务端可用，将自动选择。", AnsiColors.GREEN)
            return available_types[0]
        
        choice = Utils.show_menu(f"为 MC {mc_version} 选择服务端类型", "请选择类型:", available_display)
        return available_types[choice - 1] if choice != -1 else None
        
    def _prompt_for_mod_loader_version(
        self, server_type: ServerType, forge_versions: List, fabric_versions: List, neoforge_versions: List
    ) -> Optional[str]:
        """提示用户选择 Mod 加载器版本"""
        
        def select_from_list(name: str, display_versions: List[str], return_values: List[str]):
            if not display_versions:
                Utils.print_color(f"未能获取到 {name} 版本列表。", AnsiColors.RED)
                return None
            
            choice = Utils.show_menu(f"选择 {name} 具体版本", f"请选择 {name} 服务端版本：", display_versions)
            return return_values[choice - 1] if choice != -1 else None

        if server_type == ServerType.FORGE:
            return select_from_list(
                "Forge", 
                [v.forge_version for v in forge_versions],
                [v.full_version for v in forge_versions]
            )
        elif server_type == ServerType.FABRIC:
            return select_from_list(
                "Fabric", 
                [f"{v.version} {'(stable)' if v.stable else ''}" for v in fabric_versions],
                [v.version for v in fabric_versions]
            )
        elif server_type == ServerType.NEOFORGE:
            return select_from_list(
                "NeoForge",
                [v.neoforge_version for v in neoforge_versions],
                [v.full_version for v in neoforge_versions]
            )
        return None

    def _select_java_for_version(self, mc_version: str, server_dir: Path, java_future) -> Optional[str]:
        """为特定MC版本选择合适的Java版本"""
        java_config_file = server_dir / "java-path.json"
        
        # 检查已保存的配置
        if java_config_file.exists():
            try:
                config = json.loads(java_config_file.read_text())
                java_home = Path(config.get("javaPath", ""))
                java_exec = java_home / "bin" / "java"
                if java_exec.is_file() and os.access(java_exec, os.X_OK):
                    Utils.print_color(f"已使用为此服务器保存的Java: {java_home}", AnsiColors.GREEN)
                    return str(java_exec)
                Utils.print_color(f"警告: 保存的Java路径 {java_home} 已失效，请重新选择。", AnsiColors.YELLOW)
            except (json.JSONDecodeError, IOError) as e:
                 Utils.print_color(f"警告: 读取 java-path.json 失败: {e}，请重新选择。", AnsiColors.YELLOW)

        Utils.print_on_same_line("正在等待 Java 环境搜索结果... ")
        java_installations = java_future.result()
        Utils.clear_line()
        
        if not java_installations:
            raise IOError("未找到任何Java安装。请安装Java后重试。")
            
        display_items = [str(inst) for inst in java_installations]
        choice = Utils.show_menu(f"为 MC {mc_version} 选择 Java 版本", "请选择要使用的 Java 版本：", display_items)
        if choice == -1: return None

        selected_java = java_installations[choice - 1]
        java_path = str(selected_java.java_home / "bin" / "java")
        
        save_default = Utils.prompt_yes_no(f"是否将 {selected_java.display_alias} 设置为此服务器的默认Java?")
        if save_default:
            server_dir.mkdir(parents=True, exist_ok=True)
            config = {"javaPath": str(selected_java.java_home)}
            java_config_file.write_text(json.dumps(config, indent=2))
            Utils.print_color(f"已将 {selected_java.display_alias} 设置为此服务器的默认Java。", AnsiColors.GREEN)

        return java_path
    
    def _get_server_dir_name(self, stype: ServerType, mc_version: str, mod_version: Optional[str]) -> str:
        """生成服务器目录名"""
        if stype == ServerType.VANILLA:
            return mc_version
        
        # 清理 mod 版本号
        mod_version_sanitized = mod_version.split('-')[-1] if mod_version and '-' in mod_version else mod_version
        return f"{mc_version}-{stype.name.lower()}-{mod_version_sanitized}"

    def _install_server_core(self, stype: ServerType, mc_version: str, mod_version: Optional[str], server_dir: Path, java_path: str):
        """核心安装逻辑"""
        server_dir.mkdir(parents=True, exist_ok=True)
        Utils.print_color(f"\n正在 {server_dir.resolve()} 中安装服务器...", AnsiColors.YELLOW)

        if stype == ServerType.VANILLA:
            url = ApiClients.get_minecraft_download_url(mc_version)
            if not url: raise IOError(f"获取 Vanilla {mc_version} 下载链接失败。")
            target_jar = server_dir / f"{self._get_server_dir_name(stype, mc_version, None)}.jar"
            self._download_file(url, target_jar)
        
        elif stype == ServerType.FABRIC:
            url = ApiClients.get_fabric_installer_url()
            if not url: raise IOError("获取 Fabric 安装程序下载链接失败。")
            installer_jar = server_dir / "fabric-installer.jar"
            self._download_file(url, installer_jar)
            Utils.print_color("正在运行 Fabric 安装程序...", AnsiColors.YELLOW)
            cmd = [java_path, "-jar", installer_jar.name, "server", "-mcversion", mc_version, "-loader", mod_version, "-downloadMinecraft"]
            self._run_process(cmd, server_dir)
            installer_jar.unlink()

        elif stype in [ServerType.FORGE, ServerType.NEOFORGE]:
            if stype == ServerType.FORGE:
                version_obj = ApiClients.ForgeVersion(mod_version, mc_version, "")
                url = version_obj.get_installer_url()
            else: # NeoForge
                version_obj = ApiClients.NeoForgeVersion(mod_version, mc_version, "")
                url = version_obj.get_installer_url()
            
            installer_jar = server_dir / f"{stype.name.lower()}-installer.jar"
            self._download_file(url, installer_jar)
            Utils.print_color(f"正在运行 {stype.name} 安装程序...", AnsiColors.YELLOW)
            cmd = [java_path, "-jar", installer_jar.name, "--installServer"]
            self._run_process(cmd, server_dir)
            installer_jar.unlink()

    def _download_file(self, url: str, target: Path):
        """下载文件"""
        Utils.print_color(f"正在下载: {url}", AnsiColors.YELLOW)
        Utils.print_color(f"      到: {target.resolve()}", AnsiColors.YELLOW)
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('content-length', 0))
                downloaded = 0
                with open(target, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                             done = int(50 * downloaded / total_size)
                             percent = (downloaded / total_size) * 100
                             sys.stdout.write(f"\r[{'=' * done}{' ' * (50-done)}] {percent:.2f}%")
                             sys.stdout.flush()
            sys.stdout.write('\n')
            Utils.print_color("下载完成。", AnsiColors.GREEN)
        except requests.RequestException as e:
            raise IOError(f"下载失败: {e}")

    def _run_process(self, command: List[str], work_dir: Path):
        """在工作目录中运行一个子进程并打印其输出"""
        try:
            process = subprocess.Popen(command, cwd=work_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, encoding='utf-8', errors='replace')
            for line in iter(process.stdout.readline, ''):
                print(line, end='')
            process.wait()
            if process.returncode != 0:
                raise IOError(f"安装子进程失败，退出码: {process.returncode}")
            Utils.print_color("安装子进程成功完成。", AnsiColors.GREEN)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            raise IOError(f"执行安装命令失败: {e}")

    def _accept_eula(self, eula_file: Path):
        """自动接受 EULA"""
        if eula_file.exists():
            try:
                content = eula_file.read_text()
                if "eula=true" in content:
                    Utils.print_color("EULA 已被接受。", AnsiColors.GREEN)
                    return
            except IOError:
                pass
        
        Utils.print_color("正在创建并接受 EULA...", AnsiColors.YELLOW)
        eula_file.parent.mkdir(exist_ok=True)
        eula_file.write_text("eula=true\n")
        Utils.print_color("EULA 已接受。", AnsiColors.GREEN)
    
    def _infer_server_type(self, dir_name: str) -> ServerType:
        """从目录名推断服务器类型"""
        name = dir_name.lower()
        if "-forge-" in name: return ServerType.FORGE
        if "-fabric-" in name: return ServerType.FABRIC
        if "-neoforge-" in name: return ServerType.NEOFORGE
        return ServerType.VANILLA

    def _infer_mc_version(self, dir_name: str) -> str:
        """从目录名推断 MC 版本"""
        return dir_name.split('-')[0]

    def _start_server(self, stype: ServerType, java_path: str, server_dir: Path):
        """启动 Minecraft 服务器"""
        command: List[str] = []
        if stype in [ServerType.FORGE, ServerType.NEOFORGE]:
            # 现代 Forge/NeoForge (>=1.17) 使用 @-prefixed argument files
            # 检查特征文件 unix_args.txt
            args_files = list(server_dir.glob('**/unix_args.txt'))
            if args_files:
                args_file = args_files[0]
                # 读取 user_jvm_args.txt 中的 JVM 参数
                jvm_args_file = server_dir / 'user_jvm_args.txt'
                jvm_args = jvm_args_file.read_text().strip() if jvm_args_file.exists() else ''
                # 组合命令
                main_args = args_file.read_text().strip().replace('@user_jvm_args.txt', jvm_args)
                command = [java_path] + shlex.split(main_args)
            else: # 旧版 Forge/NeoForge 使用 run.sh
                run_script = server_dir / "run.sh"
                if not run_script.exists():
                    raise FileNotFoundError(f"启动脚本 {run_script.resolve()} 未找到!")
                os.chmod(run_script, 0o755)
                command = ["/bin/bash", str(run_script)]
        
        elif stype == ServerType.FABRIC:
            command = [java_path, "-Xmx2G", "-Xms1G", "-jar", "fabric-server-launch.jar", "nogui"]
        
        else: # Vanilla
            jar_name = f"{server_dir.name}.jar"
            command = [java_path, "-Xmx2G", "-Xms1G", "-jar", jar_name, "nogui"]
        
        Utils.print_color("\n准备启动 Minecraft 服务器...", AnsiColors.YELLOW)
        # 使用 shlex.join for proper quoting, making it copy-paste friendly
        Utils.print_color(f"工作目录: {server_dir.resolve()}", AnsiColors.CYAN)
        Utils.print_color(f"执行命令: {shlex.join(command)}", AnsiColors.CYAN)
        
        server_process = None
        try:
            # 启动服务器进程
            server_process = subprocess.Popen(
                command,
                cwd=server_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace'
            )

            # 线程：读取并打印服务器输出
            def read_output(proc):
                for line in iter(proc.stdout.readline, ''):
                    sys.stdout.write(line)
                    sys.stdout.flush()

            output_thread = threading.Thread(target=read_output, args=(server_process,))
            output_thread.daemon = True
            output_thread.start()
            
            # 主线程处理用户输入
            while server_process.poll() is None:
                try:
                    user_input = input()
                    if user_input.strip():
                        server_process.stdin.write(user_input + '\n')
                        server_process.stdin.flush()
                except (EOFError, KeyboardInterrupt):
                    Utils.print_color("\n检测到 CTRL+D/C，正在向服务器发送 'stop' 命令...", AnsiColors.YELLOW)
                    try:
                        server_process.stdin.write('stop\n')
                        server_process.stdin.flush()
                    except (IOError, BrokenPipeError):
                        # 如果管道已关闭，则进程可能已终止
                        pass
                    break

            # 等待进程终止
            server_process.wait(timeout=60)
            Utils.print_color(f"服务器进程已停止，退出码: {server_process.returncode}", AnsiColors.YELLOW)

        except (subprocess.SubprocessError, FileNotFoundError) as e:
            Utils.print_color(f"启动 Minecraft 服务器失败: {e}", AnsiColors.RED)
            if server_process and server_process.poll() is None:
                server_process.kill()
        except (KeyboardInterrupt):
             Utils.print_color("\n用户中断启动过程。", AnsiColors.YELLOW)
             if server_process and server_process.poll() is None:
                server_process.kill()

if __name__ == "__main__":
    manager = MinecraftManager()
    manager.run()
