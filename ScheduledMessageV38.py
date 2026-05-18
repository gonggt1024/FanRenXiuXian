import sys
import asyncio
import threading
import json
import os
import time
import re
from datetime import datetime, timezone
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLineEdit, QTextEdit, QPushButton,
                               QDateTimeEdit, QDateEdit, QTableWidget, QTableWidgetItem,
                               QMessageBox, QLabel, QHeaderView, QAbstractItemView, QGroupBox, QInputDialog,
                               QDialog, QDialogButtonBox, QFormLayout, QTabWidget, QScrollArea, QGridLayout,
                               QListWidget, QListWidgetItem, QSpinBox, QTextBrowser)
from PySide6.QtCore import QDateTime, Qt, QDate, Signal, QObject, QTimer, QUrl

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

DATA_FILE = "pending_tasks.json"
CONFIG_FILE = "config.json"
MAX_ACCOUNTS = 5


# ================= 自定义一站式登录对话框 =================
class LoginApiDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加新账号")
        self.resize(380, 180)
        layout = QFormLayout(self)

        self.api_id_edit = QLineEdit()
        self.api_id_edit.setPlaceholderText("例如: 1234567")
        self.api_hash_edit = QLineEdit()
        self.api_hash_edit.setPlaceholderText("该账号对应的 API Hash")

        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("例如: +8613800000000 (务必带国家区号)")

        layout.addRow("API ID:", self.api_id_edit)
        layout.addRow("API Hash:", self.api_hash_edit)
        layout.addRow("手机号码:", self.phone_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)


# ================= 万宝楼上架弹窗 =================
class WanbaoDialog(QDialog):
    def __init__(self, item_name, max_qty, parent=None):
        super().__init__(parent)
        self.setWindowTitle("万宝楼上架配置")
        self.resize(320, 200)
        layout = QFormLayout(self)

        self.item_name_label = QLabel(f"<b>{item_name}</b>")
        self.item_name_label.setStyleSheet("color: #E91E63; font-size: 14px;")

        self.sell_qty_spin = QSpinBox()
        self.sell_qty_spin.setRange(1, max_qty)
        self.sell_qty_spin.setValue(1)
        self.sell_qty_spin.setSuffix(f"  (最多 {max_qty})")

        self.target_item_edit = QLineEdit()
        self.target_item_edit.setPlaceholderText("例如: 灵石")

        self.target_qty_spin = QSpinBox()
        self.target_qty_spin.setRange(1, 99999999)
        self.target_qty_spin.setValue(1)

        layout.addRow("要上架的物品:", self.item_name_label)
        layout.addRow("上架数量:", self.sell_qty_spin)
        layout.addRow("想换取的物品:", self.target_item_edit)
        layout.addRow("换取数量:", self.target_qty_spin)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("确认上架 🚀")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.validate_and_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

        self.result_data = {}

    def validate_and_accept(self):
        target_item = self.target_item_edit.text().strip()
        if not target_item:
            QMessageBox.warning(self, "提示", "必须填写你想换取的物品名称！")
            return

        self.result_data = {
            "sell_qty": self.sell_qty_spin.value(),
            "target_item": target_item,
            "target_qty": self.target_qty_spin.value()
        }
        self.accept()


# ================= 快捷指令管理对话框 =================
class ManageCommandsDialog(QDialog):
    def __init__(self, commands_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("管理快捷指令")
        self.resize(380, 450)
        self.commands_dict = commands_dict
        self.layout = QVBoxLayout(self)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.list_widget.setStyleSheet("""
            QListWidget::item { padding: 8px; border-bottom: 1px solid #eeeeee; }
            QListWidget::item:selected { background-color: #1976D2; color: white; font-weight: bold; }
            QListWidget::item:hover { background-color: #E3F2FD; color: #000000; }
        """)

        for tab, cmds in commands_dict.items():
            for cmd in cmds:
                item = QListWidgetItem(f"[{tab}]  {cmd}")
                item.setData(Qt.UserRole, (tab, cmd))
                self.list_widget.addItem(item)

        self.btn_del = QPushButton("❌ 删除选中的指令 (支持多选)")
        self.btn_del.setStyleSheet(
            "color: white; background-color: #f44336; font-weight: bold; padding: 10px; font-size: 13px;")
        self.btn_del.clicked.connect(self.delete_selected)

        self.layout.addWidget(QLabel("<b>请点击选择要删除的指令（可按住 Ctrl 多选）：</b>"))
        self.layout.addWidget(self.list_widget)
        self.layout.addWidget(self.btn_del)

    def delete_selected(self):
        for item in self.list_widget.selectedItems():
            tab, cmd = item.data(Qt.UserRole)
            if tab in self.commands_dict and cmd in self.commands_dict[tab]:
                self.commands_dict[tab].remove(cmd)
            self.list_widget.takeItem(self.list_widget.row(item))


# ================= 信号通信 =================
class StatusSignals(QObject):
    update_row_status = Signal(int, str)
    finish_all = Signal(int, int)
    auth_request_input = Signal(str, str, str)
    auth_success = Signal(str, str, int, str)
    auth_failed = Signal(str)
    logout_success = Signal(str)
    log_signal = Signal(str)


class TelegramSchedulerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Telegram 修仙自动化 v43 (全量输出无损版)")
        self.resize(1400, 850)

        self.pending_tasks = []
        self.accounts = []
        self.active_session = None

        self.custom_commands = {"常用": [".我的灵根", ".我的储物袋", ".修炼", ".突破"]}

        self.is_listening = False
        self.listen_client = None
        self.listen_loop = None

        self.input_event = threading.Event()
        self.input_result = None

        self.signals = StatusSignals()
        self.signals.update_row_status.connect(self.on_update_row_status)
        self.signals.finish_all.connect(self.on_finish_all)
        self.signals.auth_request_input.connect(self.on_auth_request_input)
        self.signals.auth_success.connect(self.on_auth_success)
        self.signals.auth_failed.connect(self.on_auth_failed)
        self.signals.logout_success.connect(self.on_logout_success)
        self.signals.log_signal.connect(self.update_log)

        self.init_ui()
        self.load_config()
        self.update_user_ui()
        self.load_tasks_from_file()
        self.startup_check_auth()

        self.render_command_tabs()

    def get_local_timezone(self):
        return datetime.now().astimezone().tzinfo

    def update_log(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        if "<a href=" in text or "<b>" in text or "<br>" in text:
            self.log_viewer.append(f"<span style='color: #a4e400;'>[{timestamp}]</span> {text}")
        else:
            self.log_viewer.append(f"[{timestamp}] {text}")

    def init_ui(self):
        main_widget = QWidget()
        root_layout = QHBoxLayout(main_widget)

        # --- 左侧布局 ---
        left_widget = QWidget()
        layout = QVBoxLayout(left_widget)
        layout.setContentsMargins(0, 0, 10, 0)

        config_group = QGroupBox("第一步：账号与发送目标配置")
        cv = QVBoxLayout()
        user_info_lay = QVBoxLayout()
        self.lbl_all_users = QLabel("已登录所有用户：无")
        self.lbl_all_users.setStyleSheet("color: #555; font-size: 12px;")

        current_action_lay = QHBoxLayout()
        self.lbl_current_user = QLabel("当前使用：未登录")
        self.lbl_current_user.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")

        self.btn_switch = QPushButton("切换账号")
        self.btn_switch.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.btn_switch.clicked.connect(self.trigger_switch)
        self.btn_logout = QPushButton("登出当前")
        self.btn_logout.setStyleSheet("color: #d32f2f; font-weight: bold;")
        self.btn_logout.clicked.connect(self.trigger_logout)

        self.btn_login = QPushButton("添加新账号")
        self.btn_login.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.btn_login.clicked.connect(self.trigger_login)

        current_action_lay.addWidget(self.lbl_current_user);
        current_action_lay.addStretch()
        current_action_lay.addWidget(self.btn_switch);
        current_action_lay.addWidget(self.btn_logout);
        current_action_lay.addWidget(self.btn_login)
        user_info_lay.addWidget(self.lbl_all_users);
        user_info_lay.addLayout(current_action_lay)

        api_lay = QHBoxLayout()
        self.api_id_input = QLineEdit();
        self.api_id_input.setReadOnly(True)
        self.api_hash_input = QLineEdit();
        self.api_hash_input.setReadOnly(True)
        api_lay.addWidget(QLabel("专属 API (只读):"));
        api_lay.addWidget(self.api_id_input);
        api_lay.addWidget(self.api_hash_input)

        target_lay = QHBoxLayout()
        self.group_input = QLineEdit(text="ja_netfilter_group")
        self.topic_input = QLineEdit(text="7310786")
        target_lay.addWidget(QLabel("指令群组:"));
        target_lay.addWidget(self.group_input);
        target_lay.addWidget(QLabel("话题ID:"));
        target_lay.addWidget(self.topic_input)

        cv.addLayout(user_info_lay);
        cv.addWidget(QLabel("<hr>"));
        cv.addLayout(api_lay);
        cv.addLayout(target_lay)
        config_group.setLayout(cv)
        layout.addWidget(config_group)

        edit_group = QGroupBox("第二步：新增排期任务")
        ev = QVBoxLayout()
        self.msg_content = QTextEdit()
        self.msg_content.setPlaceholderText("消息内容...")
        self.msg_content.setMaximumHeight(80)
        tl = QHBoxLayout()
        self.dt_edit = QDateTimeEdit(QDateTime.currentDateTime());
        self.dt_edit.setCalendarPopup(True)
        self.add_btn = QPushButton("➕ 添加到待发列表");
        self.add_btn.clicked.connect(self.add_to_list)
        tl.addWidget(QLabel("时间:"));
        tl.addWidget(self.dt_edit);
        tl.addWidget(self.add_btn)
        ev.addWidget(self.msg_content);
        ev.addLayout(tl)
        edit_group.setLayout(ev)
        layout.addWidget(edit_group)

        batch_group = QGroupBox("💡 批量平移工具")
        bl = QHBoxLayout()
        self.target_date_edit = QDateEdit(QDate.currentDate());
        self.target_date_edit.setCalendarPopup(True)
        self.batch_update_btn = QPushButton("📅 将队列平移至选定日期");
        self.batch_update_btn.clicked.connect(self.batch_modify_date)
        bl.addWidget(QLabel("目标日期:"));
        bl.addWidget(self.target_date_edit);
        bl.addWidget(self.batch_update_btn);
        bl.addStretch()
        batch_group.setLayout(bl);
        layout.addWidget(batch_group)

        lh = QHBoxLayout()
        lh.addWidget(QLabel("<b>待发送排期队列</b>"))
        self.del_btn = QPushButton("❌ 删除选中");
        self.del_btn.clicked.connect(self.delete_selected)
        self.clear_btn = QPushButton("🗑️ 清空所有");
        self.clear_btn.clicked.connect(self.clear_all)
        lh.addStretch();
        lh.addWidget(self.del_btn);
        lh.addWidget(self.clear_btn)
        layout.addLayout(lh)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["预定发送时间", "话题 ID", "提交状态", "内容摘要"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        action_lay = QHBoxLayout()
        self.send_btn = QPushButton("🚀 同步至 [当前] 账号")
        self.send_btn.setStyleSheet(
            "background-color: #0088cc; color: white; font-size: 15px; font-weight: bold; padding: 12px;")
        self.send_btn.clicked.connect(self.run_batch_send)
        self.send_all_btn = QPushButton("🌍 批量同步至 [所有] 账号")
        self.send_all_btn.setStyleSheet(
            "background-color: #009688; color: white; font-size: 15px; font-weight: bold; padding: 12px;")
        self.send_all_btn.clicked.connect(self.run_batch_send_all)
        action_lay.addWidget(self.send_btn)
        action_lay.addWidget(self.send_all_btn)
        layout.addLayout(action_lay)

        # --- 右侧布局 (跨频道监听面板 + 快捷控制面板) ---
        right_panel = QGroupBox("⚡ 智能面板与跨域追踪")
        rv = QVBoxLayout()

        track_lay = QHBoxLayout()
        track_lay.addWidget(QLabel("<b>步骤A: 监听配置 (必填)</b>"))
        rv.addLayout(track_lay)

        rv.addWidget(QLabel("1. 目标频道/机器人名 (逗号分隔):", styleSheet="color: #666; font-size: 11px;"))
        self.target_users_input = QLineEdit(text="韩天尊, hantianzunhl")
        rv.addWidget(self.target_users_input)

        rv.addWidget(QLabel("2. 游戏内角色名 (已自动获取，可手动修改):",
                            styleSheet="color: #666; font-size: 11px; font-weight: bold;"))
        self.char_name_input = QLineEdit()
        self.char_name_input.setStyleSheet("background-color: #e3f2fd; color: #1565c0; font-weight: bold;")
        rv.addWidget(self.char_name_input)

        rv.addWidget(QLabel("<b>步骤B: 控制后台监听</b>"))
        self.btn_listen = QPushButton("🎧 开启跨域实时追踪")
        self.btn_listen.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; padding: 8px;")
        self.btn_listen.clicked.connect(self.toggle_listening)
        rv.addWidget(self.btn_listen)

        # 【核心修复】：使用 QTextBrowser 代替 QTextEdit，并设置禁用默认跳转
        self.log_viewer = QTextBrowser()
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setOpenLinks(False)
        self.log_viewer.setStyleSheet(
            "background-color: #1e1e1e; color: #a4e400; font-family: 'Consolas'; font-size: 13px;")
        self.log_viewer.anchorClicked.connect(self.handle_log_link)
        rv.addWidget(self.log_viewer)

        rv.addWidget(QLabel("<hr>"))

        # ================= 快捷指令发送面板 UI =================
        rv.addWidget(QLabel("<b>🚀 快捷指令面板 (点击按钮直发)</b>"))

        self.cmd_tabs = QTabWidget()
        self.cmd_tabs.setMaximumHeight(150)
        rv.addWidget(self.cmd_tabs)

        cmd_input_lay = QHBoxLayout()
        self.instant_msg_input = QLineEdit()
        self.instant_msg_input.setPlaceholderText("输入指令直接发送，或添加到分组按钮...")
        self.btn_instant_send = QPushButton("↗ 发送")
        self.btn_instant_send.setStyleSheet("background-color: #E91E63; color: white; font-weight: bold;")
        self.btn_instant_send.clicked.connect(self.send_instant_msg)
        cmd_input_lay.addWidget(self.instant_msg_input, stretch=4)
        cmd_input_lay.addWidget(self.btn_instant_send, stretch=1)
        rv.addLayout(cmd_input_lay)

        tab_ctrl_lay = QHBoxLayout()
        self.btn_add_cmd = QPushButton("➕ 添为按钮");
        self.btn_add_cmd.clicked.connect(self.add_custom_command)
        self.btn_add_tab = QPushButton("📁 新建分组");
        self.btn_add_tab.clicked.connect(self.add_new_tab)
        self.btn_del_tab = QPushButton("🗑️ 删分组");
        self.btn_del_tab.setStyleSheet("color: #d32f2f;");
        self.btn_del_tab.clicked.connect(self.delete_current_tab)
        self.btn_manage_cmd = QPushButton("⚙️ 管理");
        self.btn_manage_cmd.clicked.connect(self.manage_custom_commands)
        self.btn_guide = QPushButton("📖 《天道总纲》")
        self.btn_guide.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        self.btn_guide.clicked.connect(self.copy_guide_link)

        tab_ctrl_lay.addWidget(self.btn_add_cmd);
        tab_ctrl_lay.addWidget(self.btn_add_tab)
        tab_ctrl_lay.addWidget(self.btn_del_tab);
        tab_ctrl_lay.addWidget(self.btn_manage_cmd);
        tab_ctrl_lay.addWidget(self.btn_guide)
        rv.addLayout(tab_ctrl_lay)

        right_panel.setLayout(rv)
        root_layout.addWidget(left_widget, 6);
        root_layout.addWidget(right_panel, 4)
        self.setCentralWidget(main_widget)

    # ================= UI 交互逻辑 =================
    def copy_guide_link(self):
        QApplication.clipboard().setText("https://linux.do/t/topic/888560")
        self.btn_guide.setText("✅ 复制成功，请打开浏览器！")
        self.btn_guide.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.signals.log_signal.emit("🔗 已成功复制《天道总纲》链接，请到浏览器中粘贴打开。")
        QTimer.singleShot(2500, self.revert_guide_btn)

    def revert_guide_btn(self):
        self.btn_guide.setText("📖 《天道总纲》")
        self.btn_guide.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")

    def render_command_tabs(self):
        current_index = self.cmd_tabs.currentIndex()
        self.cmd_tabs.clear()

        for tab_name, cmds in self.custom_commands.items():
            tab_widget = QWidget()
            grid = QGridLayout(tab_widget)
            grid.setContentsMargins(5, 5, 5, 5)
            row, col = 0, 0
            for cmd in cmds:
                btn = QPushButton(cmd)
                btn.setStyleSheet(
                    "background-color: #2196F3; color: white; padding: 6px; font-weight: bold; border-radius: 4px;")
                btn.clicked.connect(lambda checked, c=cmd: self.send_quick_command(c))
                grid.addWidget(btn, row, col)
                col += 1
                if col >= 4: col = 0; row += 1
            grid.setRowStretch(row + 1, 1)
            scroll = QScrollArea();
            scroll.setWidgetResizable(True);
            scroll.setWidget(tab_widget)
            self.cmd_tabs.addTab(scroll, tab_name)

        if current_index >= 0 and current_index < self.cmd_tabs.count():
            self.cmd_tabs.setCurrentIndex(current_index)

    def add_new_tab(self):
        text, ok = QInputDialog.getText(self, "新建分组", "请输入新分组的名称:")
        if ok and text.strip():
            tab_name = text.strip()
            if tab_name not in self.custom_commands:
                self.custom_commands[tab_name] = []
                self.save_config();
                self.render_command_tabs();
                self.cmd_tabs.setCurrentIndex(self.cmd_tabs.count() - 1)
            else:
                QMessageBox.warning(self, "提示", "该分组已存在！")

    def delete_current_tab(self):
        current_tab_name = self.cmd_tabs.tabText(self.cmd_tabs.currentIndex())
        if not current_tab_name: return
        if QMessageBox.question(self, "确认删除",
                                f"确定要删除分组【{current_tab_name}】及其中所有指令吗？") == QMessageBox.Yes:
            del self.custom_commands[current_tab_name]
            if not self.custom_commands: self.custom_commands["常用"] = []
            self.save_config();
            self.render_command_tabs()

    def add_custom_command(self):
        content = self.instant_msg_input.text().strip()
        if not content: return
        current_tab_name = self.cmd_tabs.tabText(self.cmd_tabs.currentIndex()) or "常用"
        if current_tab_name not in self.custom_commands: self.custom_commands[current_tab_name] = []
        if content not in self.custom_commands[current_tab_name]:
            self.custom_commands[current_tab_name].append(content)
            self.save_config();
            self.render_command_tabs();
            self.instant_msg_input.clear()
            self.signals.log_signal.emit(f"✅ 已将 '{content}' 添加至【{current_tab_name}】分组！")

    def manage_custom_commands(self):
        ManageCommandsDialog(self.custom_commands, self).exec()
        self.save_config();
        self.render_command_tabs()

    def send_instant_msg(self):
        content = self.instant_msg_input.text().strip()
        if not content: return
        self.instant_msg_input.clear();
        self.send_quick_command(content)

    def send_quick_command(self, content):
        if not self.active_session: return
        group = self.group_input.text().strip()
        topic_id = int(self.topic_input.text().strip()) if self.topic_input.text().strip() else None
        self.signals.log_signal.emit(f"⚡ 发送指令: {content}")
        threading.Thread(target=lambda: asyncio.run(self._instant_send_thread(group, topic_id, content)),
                         daemon=True).start()

    async def _instant_send_thread(self, group, topic_id, content):
        acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
        if not acc: return
        if self.is_listening and self.listen_client and self.listen_loop:
            try:
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
                    self.listen_client.send_message(group, content, reply_to=topic_id), self.listen_loop))
            except Exception as e:
                self.signals.log_signal.emit(f"❌ 发送失败: {e}")
            return
        proxy = ("socks5", "127.0.0.1", 3067)
        client = TelegramClient(acc['session'], int(acc['api_id']), acc['api_hash'], proxy=proxy)
        try:
            await client.start(); await client.send_message(group, content, reply_to=topic_id)
        except Exception as e:
            self.signals.log_signal.emit(f"❌ 发送失败: {e}")
        finally:
            await client.disconnect()

    # ================= 【核心拦截】：处理日志里物品名称的点击 =================
    def handle_log_link(self, url: QUrl):
        url_str = url.toString()
        if url_str.startswith("sell:"):
            parts = url_str.split(':', 2)
            if len(parts) == 3:
                item_name = parts[1]
                try:
                    max_qty = int(parts[2])
                except:
                    max_qty = 1

                dialog = WanbaoDialog(item_name, max_qty, self)
                if dialog.exec() == QDialog.Accepted:
                    data = dialog.result_data
                    self.send_wanbao_command(item_name, data['sell_qty'], data['target_item'], data['target_qty'])

    def send_wanbao_command(self, item_name, sell_qty, target_item, target_qty):
        command = f".上架 {item_name}*{sell_qty} 换 {target_item}*{target_qty}"
        self.send_quick_command(command)

    # ================= 跨域追踪监听逻辑核心 =================
    def toggle_listening(self):
        if not self.target_users_input.text().strip(): return
        if not self.is_listening:
            self.is_listening = True
            self.btn_listen.setText("🛑 停止监听")
            self.btn_listen.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 8px;")
            targets = [t.strip() for t in self.target_users_input.text().split(',') if t.strip()]
            self.update_log(f"系统：监听已启动。目标：{', '.join(targets)}")
            custom_char_name = self.char_name_input.text().strip()
            threading.Thread(target=lambda: asyncio.run(self.start_monitor_loop(targets, custom_char_name)),
                             daemon=True).start()
        else:
            self.is_listening = False
            self.btn_listen.setText("🎧 开启跨域实时追踪")
            self.btn_listen.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; padding: 8px;")

    async def start_monitor_loop(self, targets, custom_char_name):
        self.listen_loop = asyncio.get_running_loop()
        acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
        self.listen_client = TelegramClient(acc['session'], int(acc['api_id']), acc['api_hash'],
                                            proxy=("socks5", "127.0.0.1", 3067))

        try:
            await self.listen_client.start()
            me = await self.listen_client.get_me()
            my_id = me.id

            my_names = set([me.first_name, me.username])
            if me.first_name: my_names.add(me.first_name.split()[0])
            if custom_char_name: my_names.add(custom_char_name)

            self.signals.log_signal.emit(f"✅ 通道就绪。挂载角色名识别: {', '.join([n for n in my_names if n])}")

            @self.listen_client.on(events.NewMessage(incoming=True))
            @self.listen_client.on(events.MessageEdited(incoming=True))
            async def handler(event):
                sender = await event.get_sender()
                chat = await event.get_chat()
                s_info = []
                if sender: s_info.extend(
                    [getattr(sender, 'username', ''), getattr(sender, 'id', ''), getattr(sender, 'title', ''),
                     getattr(sender, 'first_name', '')])
                if chat: s_info.extend(
                    [getattr(chat, 'username', ''), getattr(chat, 'id', ''), getattr(chat, 'title', '')])
                s_info_strs = [str(info) for info in s_info if info is not None]

                if not any(str(t) in info_str for t in targets for info_str in s_info_strs): return

                text = event.text or event.raw_text or ""
                if not text: return

                is_mine = False
                hit_reason = ""

                if getattr(event, 'is_reply', False):
                    rm = await event.get_reply_message()
                    if rm and rm.sender_id == my_id:
                        is_mine = True;
                        hit_reason = "指令回复"
                if not is_mine and getattr(event, 'mentioned', False):
                    is_mine = True;
                    hit_reason = "原生艾特"

                if not is_mine:
                    for n in my_names:
                        if not n: continue
                        if re.search(rf'@{re.escape(n)}(?![a-zA-Z0-9_])', text, re.IGNORECASE) or \
                                re.search(rf'{re.escape(n)}\s*的(天命|身份|储物袋)', text, re.IGNORECASE):
                            is_mine = True;
                            hit_reason = f"精准名字 ({n})"
                            break

                if not is_mine: return
                source = getattr(sender, 'title', '') or getattr(sender, 'first_name', '') or getattr(chat, 'title',
                                                                                                      '') or '未知'

                # ====================================================
                # 专属规则 1: 处理储物袋，生成可点击蓝字
                # ====================================================
                if "的储物袋" in text:
                    self.signals.log_signal.emit(f"🌟 截获 [{source}] 的储物袋 (点击蓝色物品上架):\n{'=' * 30}")
                    html_text = text.replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br>')

                    def link_replacer(match):
                        item_name = match.group(1).strip()
                        item_qty = match.group(2)
                        return f"- <a href='sell:{item_name}:{item_qty}' style='color: #29B6F6; font-weight: bold; text-decoration: underline;'>{item_name}</a> x {item_qty}"

                    html_text = re.sub(r'-\s+([^\n<]+?)\s+x\s+(\d+)', link_replacer, html_text)
                    self.signals.log_signal.emit(f"{html_text}<br>{'=' * 30}")

                # ====================================================
                # 专属规则 2: 处理属性面板
                # ====================================================
                elif "的天命玉牒" in text or "修为" in text:
                    self.signals.log_signal.emit(f"🌟 截获 [{source}] 属性面板:\n{text}\n{'=' * 30}")
                    try:
                        title_match = re.search(r'(?:称号[：:]|【称号】)\s*([^\n]+)', text)
                        magic_match = re.search(r'(?:当前祭出[：:]|【当前祭出】)\s*([^\n]+)', text)
                        sect_match = re.search(r'(?:宗门[：:]|【宗门】)\s*([^\n]+)', text)
                        root_match = re.search(r'(?:灵根[：:]?|【灵根】)\s*([^\n]+)', text)
                        cult_match = re.search(r'(?:修为[：:]|【修为】)\s*(\d+)\s*/\s*(\d+)', text)
                        tox_match = re.search(r'(?:丹毒[：:]|【丹毒】)\s*(\d+)', text)

                        def cl(m):
                            return re.sub(r'[【\[\]】]', '', m.group(1)).strip() if m else "无"

                        self.signals.log_signal.emit(" 📊 [属性自动解包]")
                        self.signals.log_signal.emit(f" ➣ 称号: {cl(title_match)}")
                        self.signals.log_signal.emit(f" ➣ 法宝: {cl(magic_match)}")
                        self.signals.log_signal.emit(f" ➣ 宗门: {cl(sect_match)}")
                        self.signals.log_signal.emit(f" ➣ 灵根: {cl(root_match)}")
                        self.signals.log_signal.emit(
                            f" ➣ 修为: {int(cult_match.group(1))} / {int(cult_match.group(2))}" if cult_match else " ➣ 修为: 0 / 0")
                        self.signals.log_signal.emit(f" ➣ 丹毒: {int(tox_match.group(1)) if tox_match else 0}")
                        self.signals.log_signal.emit("-" * 30)
                    except Exception as ex:
                        self.signals.log_signal.emit(f" ❌ 解析异常: {ex}")

                # ====================================================
                # 原有默认规则: 其他任何消息均 100% 完整输出
                # ====================================================
                else:
                    # 彻底移除 `[:50]` 的截断，保留所有的原文内容
                    self.signals.log_signal.emit(f"📥 [{source}] {hit_reason}:\n{text}\n{'=' * 30}")

            while self.is_listening:
                await asyncio.sleep(1)
                if not self.listen_client.is_connected(): break
        except Exception as e:
            self.signals.log_signal.emit(f"❌ 监听异常: {e}")
        finally:
            await self.listen_client.disconnect();
            self.is_listening = False

    # ================= 账号管理逻辑 =================

    def update_user_ui(self):
        if not self.accounts:
            self.lbl_all_users.setText("已登录所有用户：无")
            self.lbl_current_user.setText("当前使用：未登录")
            self.lbl_current_user.setStyleSheet("color: gray; font-weight: bold; font-size: 14px;")
            self.btn_logout.setVisible(False)
            self.btn_switch.setVisible(False)
            self.btn_login.setVisible(True)
            self.api_id_input.clear()
            self.api_hash_input.clear()
            self.char_name_input.clear()
        else:
            names = [acc['name'] for acc in self.accounts]
            self.lbl_all_users.setText(f"已登录所有用户：{', '.join(names)}")
            current_acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
            current_name = current_acc['name'] if current_acc else "未知"
            self.lbl_current_user.setText(f"当前使用：{current_name}")
            self.lbl_current_user.setStyleSheet("color: #00796b; font-weight: bold; font-size: 15px;")

            if current_acc:
                self.api_id_input.setText(str(current_acc.get('api_id', '')))
                self.api_hash_input.setText(str(current_acc.get('api_hash', '')))
                if not self.char_name_input.text().strip() and current_acc.get('name'):
                    self.char_name_input.setText(current_acc['name'].split()[0])

            self.btn_logout.setVisible(True)
            self.btn_switch.setVisible(len(self.accounts) > 1)
            self.btn_login.setVisible(len(self.accounts) < MAX_ACCOUNTS)
        self.save_config()

    def trigger_switch(self):
        names = [acc['name'] for acc in self.accounts]
        current_name = next((a['name'] for a in self.accounts if a['session'] == self.active_session), "")
        item, ok = QInputDialog.getItem(self, "切换账号", "选择要切换的账号:", names,
                                        names.index(current_name) if current_name in names else 0, False)
        if ok and item:
            target_acc = next((a for a in self.accounts if a['name'] == item), None)
            if target_acc:
                self.active_session = target_acc['session']
                self.char_name_input.clear()
                self.update_user_ui()

    def trigger_login(self):
        dialog = LoginApiDialog(self)
        if dialog.exec():
            api_id_str, api_hash, phone = dialog.api_id_edit.text().strip(), dialog.api_hash_edit.text().strip(), dialog.phone_edit.text().strip()
            if not api_id_str or not api_hash or not phone: return
            try:
                api_id = int(api_id_str)
            except:
                return
            self.save_config()
            self.btn_login.setEnabled(False)
            threading.Thread(target=lambda: asyncio.run(self.login_logic(api_id, api_hash, phone)), daemon=True).start()

    def trigger_logout(self):
        if QMessageBox.question(self, "确认登出", "确定登出当前账号吗？",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.btn_logout.setEnabled(False)
            threading.Thread(target=lambda: asyncio.run(self.logout_logic()), daemon=True).start()

    def on_update_row_status(self, row, status):
        item = QTableWidgetItem(status)
        if "✅" in status:
            item.setForeground(Qt.darkGreen)
        elif "❌" in status:
            item.setForeground(Qt.red)
        elif "⚠️" in status:
            item.setForeground(Qt.darkYellow)
        self.table.setItem(row, 2, item)

    def on_finish_all(self, success, fail):
        self.send_btn.setEnabled(True)
        self.send_all_btn.setEnabled(True)
        QMessageBox.information(self, "完成", f"同步流程结束！\n成功/部分成功: {success} 条\n全部失败: {fail} 条")

    def on_auth_request_input(self, title, prompt, mode):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title);
        dialog.setLabelText(prompt)
        if mode == "password": dialog.setTextEchoMode(QLineEdit.Password)
        ok = dialog.exec()
        self.input_result = dialog.textValue().strip() if ok else None
        self.input_event.set()

    def on_auth_success(self, session_name, nickname, api_id, api_hash):
        existing = next((a for a in self.accounts if a['session'] == session_name), None)
        if not existing:
            self.accounts.append({"session": session_name, "name": nickname, "api_id": api_id, "api_hash": api_hash})
        else:
            existing.update({"name": nickname, "api_id": api_id, "api_hash": api_hash})
        self.active_session = session_name
        self.char_name_input.clear()
        self.update_user_ui()

    def on_auth_failed(self, msg):
        self.update_user_ui()
        if msg: QMessageBox.warning(self, "登录失败", msg)

    def on_logout_success(self, session_name):
        self.accounts = [a for a in self.accounts if a['session'] != session_name]
        self.active_session = self.accounts[0]['session'] if self.accounts else None
        self.char_name_input.clear()
        self.update_user_ui()
        QMessageBox.information(self, "提示", "已成功登出该账号。")

    async def login_logic(self, api_id, api_hash, phone):
        new_sess = f"session_{int(time.time())}"
        client = TelegramClient(new_sess, api_id, api_hash, proxy=("socks5", "127.0.0.1", 3067))
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(phone)
                self.input_event.clear();
                self.signals.auth_request_input.emit("验证码", "请输入验证码:", "normal")
                self.input_event.wait();
                code = self.input_result
                try:
                    await client.sign_in(phone, code)
                except SessionPasswordNeededError:
                    self.input_event.clear();
                    self.signals.auth_request_input.emit("2FA", "请输入密码:", "password")
                    self.input_event.wait();
                    await client.sign_in(password=self.input_result)
            me = await client.get_me()
            self.signals.auth_success.emit(new_sess, f"{me.first_name} {me.last_name or ''}".strip(), api_id, api_hash)
        except Exception as e:
            self.signals.auth_failed.emit(str(e))
        finally:
            await client.disconnect()

    async def logout_logic(self):
        if not self.active_session: return
        acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
        if not acc: return
        try:
            client = TelegramClient(self.active_session, int(acc['api_id']), acc['api_hash'],
                                    proxy=("socks5", "127.0.0.1", 3067))
            await client.connect();
            await client.log_out()
            if os.path.exists(f"{self.active_session}.session"): os.remove(f"{self.active_session}.session")
        except:
            pass
        finally:
            self.signals.logout_success.emit(self.active_session)

    def startup_check_auth(self):
        if self.active_session and self.accounts:
            acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
            if acc: threading.Thread(target=lambda: asyncio.run(self.check_auth_logic(acc)), daemon=True).start()

    async def check_auth_logic(self, acc):
        try:
            client = TelegramClient(acc['session'], int(acc['api_id']), acc['api_hash'],
                                    proxy=("socks5", "127.0.0.1", 3067))
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                self.signals.auth_success.emit(acc['session'], f"{me.first_name} {me.last_name or ''}".strip(),
                                               int(acc['api_id']), acc['api_hash'])
            else:
                self.signals.auth_failed.emit("")
            await client.disconnect()
        except:
            self.signals.auth_failed.emit("")

    # ================= 批量发送逻辑 =================
    async def send_task_logic(self):
        self.save_config()
        acc = next((a for a in self.accounts if a['session'] == self.active_session), None)
        if not acc: self.signals.finish_all.emit(0, 0); return

        group = self.group_input.text().strip()
        local_tz = self.get_local_timezone()

        async def _do_send(client):
            s, f = 0, 0
            for i, task in enumerate(self.pending_tasks):
                self.signals.update_row_status.emit(i, "正在提交...")
                try:
                    await client.send_message(group, task['content'], schedule=task['time'].replace(tzinfo=local_tz),
                                              reply_to=task['topic'])
                    self.signals.update_row_status.emit(i, "✅ 成功");
                    s += 1
                except:
                    self.signals.update_row_status.emit(i, "❌ 失败"); f += 1
                await asyncio.sleep(0.7)
            return s, f

        if self.is_listening and self.listen_client and self.listen_loop:
            s, f = await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(_do_send(self.listen_client), self.listen_loop))
            self.signals.finish_all.emit(s, f);
            return

        client = TelegramClient(acc['session'], int(acc['api_id']), acc['api_hash'],
                                proxy=("socks5", "127.0.0.1", 3067))
        try:
            await client.start(); s, f = await _do_send(client)
        except:
            s, f = 0, len(self.pending_tasks)
        finally:
            await client.disconnect(); self.signals.finish_all.emit(s, f)

    def run_batch_send(self):
        if not self.active_session: return
        self.send_btn.setEnabled(False);
        self.send_all_btn.setEnabled(False)
        threading.Thread(target=lambda: asyncio.run(self.send_task_logic()), daemon=True).start()

    async def send_all_accounts_logic(self):
        group = self.group_input.text().strip()
        local_tz = self.get_local_timezone()
        s, f = 0, 0
        clients = []
        for acc in self.accounts:
            c = TelegramClient(acc['session'], int(acc['api_id']), acc['api_hash'], proxy=("socks5", "127.0.0.1", 3067))
            try:
                await c.connect()
                if await c.is_user_authorized():
                    clients.append(c)
                else:
                    await c.disconnect()
            except:
                pass

        if not clients: return

        try:
            for i, task in enumerate(self.pending_tasks):
                self.signals.update_row_status.emit(i, "分发中...")
                ts = 0
                for c in clients:
                    try:
                        await c.send_message(group, task['content'], schedule=task['time'].replace(tzinfo=local_tz),
                                             reply_to=task['topic']); ts += 1
                    except:
                        pass
                    await asyncio.sleep(0.5)
                if ts == len(clients):
                    self.signals.update_row_status.emit(i, "✅ 成功"); s += 1
                elif ts > 0:
                    self.signals.update_row_status.emit(i, "⚠️ 部分成功"); s += 1
                else:
                    self.signals.update_row_status.emit(i, "❌ 失败"); f += 1
        finally:
            for c in clients: await c.disconnect()
            self.signals.finish_all.emit(s, f)

    def run_batch_send_all(self):
        if not self.accounts: return
        self.send_btn.setEnabled(False);
        self.send_all_btn.setEnabled(False)
        threading.Thread(target=lambda: asyncio.run(self.send_all_accounts_logic()), daemon=True).start()

    def batch_modify_date(self):
        new_date = self.target_date_edit.date().toPython()
        for task in self.pending_tasks: task['time'] = task['time'].replace(year=new_date.year, month=new_date.month,
                                                                            day=new_date.day)
        self.refresh_table();
        self.save_tasks_to_file()

    def refresh_table(self):
        self.table.setRowCount(0)
        for task in self.pending_tasks: self.update_table_row(task)

    def save_config(self):
        c = {
            "group": self.group_input.text(),
            "topic": self.topic_input.text(),
            "accounts": self.accounts,
            "active_session": self.active_session,
            "custom_commands": self.custom_commands,
            "char_name": self.char_name_input.text().strip()
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f: json.dump(c, f, ensure_ascii=False)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    c = json.load(f);
                    self.accounts = c.get("accounts", []);
                    self.active_session = c.get("active_session")
                    self.group_input.setText(c.get("group", "ja_netfilter_group"));
                    self.topic_input.setText(c.get("topic", "7310786"))
                    if "custom_commands" in c: self.custom_commands = c["custom_commands"]
                    if "char_name" in c: self.char_name_input.setText(c["char_name"])
            except:
                pass

    def save_tasks_to_file(self):
        s = [{"time": t['time'].isoformat(), "content": t['content'], "topic": t['topic']} for t in self.pending_tasks]
        with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(s, f, ensure_ascii=False, indent=4)

    def load_tasks_from_file(self):
        if not os.path.exists(DATA_FILE): return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    t = {"time": datetime.fromisoformat(item['time']), "content": item['content'],
                         "topic": item['topic']}
                    self.pending_tasks.append(t);
                    self.update_table_row(t)
        except:
            pass

    def update_table_row(self, task):
        row = self.table.rowCount();
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(task['time'].strftime("%Y-%m-%d %H:%M")))
        self.table.setItem(row, 1, QTableWidgetItem(str(task['topic'])))
        self.table.setItem(row, 2, QTableWidgetItem("等待中"))
        self.table.setItem(row, 3, QTableWidgetItem(task['content'][:50].replace('\n', ' ')))

    def add_to_list(self):
        dt = self.dt_edit.dateTime().toPython().replace(second=0, microsecond=0)
        content = self.msg_content.toPlainText().strip()
        topic = self.topic_input.text().strip()
        if not content or not topic: return
        t = {"time": dt, "content": content, "topic": int(topic)}
        self.pending_tasks.append(t);
        self.update_table_row(t);
        self.save_tasks_to_file();
        self.msg_content.clear()

    def delete_selected(self):
        row = self.table.currentRow()
        if row >= 0: self.pending_tasks.pop(row); self.table.removeRow(row); self.save_tasks_to_file()

    def clear_all(self):
        if self.pending_tasks and QMessageBox.question(self, "确认", "确定清空列表吗？") == QMessageBox.Yes:
            self.pending_tasks.clear();
            self.table.setRowCount(0)
            if os.path.exists(DATA_FILE): os.remove(DATA_FILE)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TelegramSchedulerGUI()
    window.show()
    sys.exit(app.exec())