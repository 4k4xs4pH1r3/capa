# Copyright (C) 2020 FireEye, Inc. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
# You may obtain a copy of the License at: [package root]/LICENSE.txt
# Unless required by applicable law or agreed to in writing, software distributed under the License
#  is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and limitations under the License.

import os
import json
import logging
import collections

import idaapi
import ida_settings
from PyQt5 import QtGui, QtCore, QtWidgets

import capa.main
import capa.rules
import capa.ida.helpers
import capa.render.utils as rutils
import capa.features.extractors.ida
from capa.ida.plugin.icon import QICON
from capa.ida.plugin.view import CapaExplorerQtreeView
from capa.ida.plugin.hooks import CapaExplorerIdaHooks
from capa.ida.plugin.model import CapaExplorerDataModel
from capa.ida.plugin.proxy import CapaExplorerRangeProxyModel, CapaExplorerSearchProxyModel

logger = logging.getLogger(__name__)
settings = ida_settings.IDASettings("capa")


class ProgressUpdater(QtCore.QObject):

    progress = QtCore.pyqtSignal(str, int)

    def __init__(self):
        """ """
        super(ProgressUpdater, self).__init__()

    def update(self, text, value):
        """ """
        self.progress.emit(text, value)
        idaapi.request_refresh(0xFFFFFFFF)


class CapaExplorerIdaFeatureExtractor(capa.features.extractors.ida.IdaFeatureExtractor):
    def __init__(self, progress):
        super(CapaExplorerIdaFeatureExtractor, self).__init__()
        self.progress = progress

    def extract_function_features(self, f):
        self.progress.update("Analyzing function at 0x%x" % f.start_ea, 1)
        for (feature, ea) in capa.features.extractors.ida.function.extract_features(f):
            yield feature, ea

    def extract_basic_block_features(self, f, bb):
        self.progress.update("Analyzing basic block at 0x%x" % f.start_ea, 1)
        for (feature, ea) in capa.features.extractors.ida.basicblock.extract_features(f, bb):
            yield feature, ea

    def extract_insn_features(self, f, bb, insn):
        self.progress.update("Analyzing instruction at 0x%x" % f.start_ea, 1)
        for (feature, ea) in capa.features.extractors.ida.insn.extract_features(f, bb, insn):
            yield feature, ea


class CapaExplorerForm(idaapi.PluginForm):
    """form element for plugin interface"""

    def __init__(self, name):
        """initialize form elements"""
        print("init")
        super(CapaExplorerForm, self).__init__()

        self.form_title = name
        self.rule_path = ""

        self.parent = None
        self.ida_hooks = None
        self.doc = None

        # models
        self.model_data = None
        self.range_model_proxy = None
        self.search_model_proxy = None

        # UI controls
        self.view_limit_results_by_function = None
        self.view_search_bar = None
        self.view_tree = None
        self.view_attack = None
        self.view_tabs = None
        self.view_menu_bar = None
        self.view_info_label = None
        self.view_progress_bar = None
        self.view_buttons = None

    def OnCreate(self, form):
        """called when plugin form is created"""
        self.parent = self.FormToPyQtWidget(form)
        self.parent.setWindowIcon(QICON)
        self.load_interface()
        self.load_ida_hooks()

    def Show(self):
        """creates form if not already create, else brings plugin to front"""
        super(CapaExplorerForm, self).Show(
            self.form_title, options=(idaapi.PluginForm.WOPN_TAB | idaapi.PluginForm.WCLS_CLOSE_LATER)
        )

    def OnClose(self, form):
        """called when form is closed

        ensure any plugin modifications (e.g. hooks and UI changes) are reset before the plugin is closed
        """
        self.unload_ida_hooks()
        self.ida_reset()

    def load_interface(self):
        """load user interface"""
        # load models
        self.model_data = CapaExplorerDataModel()

        # model <- filter range <- filter search <- view

        self.range_model_proxy = CapaExplorerRangeProxyModel()
        self.range_model_proxy.setSourceModel(self.model_data)

        self.search_model_proxy = CapaExplorerSearchProxyModel()
        self.search_model_proxy.setSourceModel(self.range_model_proxy)

        self.view_tree = CapaExplorerQtreeView(self.search_model_proxy, self.parent)
        self.load_view_attack()

        # load parent tab and children tab views
        self.load_view_tabs()
        self.load_view_checkbox_limit_by()
        self.load_view_search_bar()
        self.load_view_tree_tab()
        self.load_view_attack_tab()
        self.load_view_info_label()
        self.load_view_progress_bar()
        self.load_view_buttons()

        # load menu bar and sub menus
        self.load_view_menu_bar()
        self.load_file_menu()
        self.load_rules_menu()
        self.load_view_menu()

        # load parent view
        self.load_view_parent()

    def load_view_tabs(self):
        """load tabs"""
        tabs = QtWidgets.QTabWidget()
        self.view_tabs = tabs

    def load_view_menu_bar(self):
        """load menu bar"""
        bar = QtWidgets.QMenuBar()
        self.view_menu_bar = bar

    def load_view_attack(self):
        """load MITRE ATT&CK table"""
        table_headers = [
            "ATT&CK Tactic",
            "ATT&CK Technique ",
        ]

        table = QtWidgets.QTableWidget()

        table.setColumnCount(len(table_headers))
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(QtCore.Qt.NoFocus)
        table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        table.setHorizontalHeaderLabels(table_headers)
        table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft)
        table.setShowGrid(False)
        table.setStyleSheet("QTableWidget::item { padding: 25px; }")

        self.view_attack = table

    def load_view_checkbox_limit_by(self):
        """load limit results by function checkbox"""
        check = QtWidgets.QCheckBox("Limit results to current function")
        check.setChecked(False)
        check.stateChanged.connect(self.slot_checkbox_limit_by_changed)

        self.view_limit_results_by_function = check

    def load_view_info_label(self):
        """load rules label"""
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignLeft)
        label.setText("No rules loaded")

        self.view_info_label = label

    def load_view_progress_bar(self):
        """ """
        bar = QtWidgets.QProgressBar(self.parent)
        bar.setAlignment(QtCore.Qt.AlignLeft)
        bar.setValue(0)
        bar.setOrientation(QtCore.Qt.Horizontal)

        self.view_progress_bar = bar

    def load_view_search_bar(self):
        """load the search bar control"""
        line = QtWidgets.QLineEdit()
        line.setPlaceholderText("search...")
        line.textChanged.connect(self.slot_limit_results_to_search)

        self.view_search_bar = line

    def load_view_buttons(self):
        """ """
        analyze_button = QtWidgets.QPushButton("Analyze")
        reset_button = QtWidgets.QPushButton("Reset")

        analyze_button.clicked.connect(self.slot_analyze)
        reset_button.clicked.connect(self.slot_reset)

        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(analyze_button)
        layout.addWidget(reset_button)
        layout.addStretch(1)

        self.view_buttons = layout


    def load_view_parent(self):
        """load view parent"""
        layout = QtWidgets.QVBoxLayout()

        layout.addWidget(self.view_tabs)
        layout.addWidget(self.view_info_label)
        layout.addWidget(self.view_progress_bar)
        layout.addLayout(self.view_buttons)
        layout.setMenuBar(self.view_menu_bar)

        self.hide_progress()

        self.parent.setLayout(layout)

    def load_view_tree_tab(self):
        """load tree view tab"""
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.view_limit_results_by_function)
        layout.addWidget(self.view_search_bar)
        layout.addWidget(self.view_tree)

        tab = QtWidgets.QWidget()
        tab.setLayout(layout)

        self.view_tabs.addTab(tab, "Tree View")

    def load_view_attack_tab(self):
        """load MITRE ATT&CK view tab"""
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.view_attack)

        tab = QtWidgets.QWidget()
        tab.setLayout(layout)

        self.view_tabs.addTab(tab, "MITRE")

    def load_file_menu(self):
        """load file menu controls"""
        actions = (
            #("Rerun analysis", "Rerun capa analysis on current database", self.slot_reload),
            ("Export results...", "Export capa results as JSON file", self.slot_export_json),
        )
        self.load_menu("File", actions)

    def load_rules_menu(self):
        """load rules menu controls"""
        actions = (("Change rules directory...", "Select new rules directory", self.slot_change_rules_dir),)
        self.load_menu("Rules", actions)

    def load_view_menu(self):
        """load view menu controls"""
        pass
        #actions = (("Reset view", "Reset plugin view", self.slot_reset),)
        #self.load_menu("View", actions)

    def load_menu(self, title, actions):
        """load menu actions

        @param title: menu name displayed in UI
        @param actions: tuple of tuples containing action name, tooltip, and slot function
        """
        menu = self.view_menu_bar.addMenu(title)
        for (name, _, slot) in actions:
            action = QtWidgets.QAction(name, self.parent)
            action.triggered.connect(slot)
            menu.addAction(action)

    def slot_export_json(self):
        """export capa results as JSON file"""
        if not self.doc:
            idaapi.info("No capa results to export.")
            return

        path = idaapi.ask_file(True, "*.json", "Choose file")

        # user cancelled, entered blank input, etc.
        if not path:
            return

        # check file exists, ask to override
        if os.path.exists(path) and 1 != idaapi.ask_yn(1, "File already exists. Overwrite?"):
            return

        with open(path, "wb") as export_file:
            export_file.write(
                json.dumps(self.doc, sort_keys=True, cls=capa.render.CapaJsonObjectEncoder).encode("utf-8")
            )

    def load_ida_hooks(self):
        """load IDA UI hooks"""

        # map named action (defined in idagui.cfg) to Python function
        action_hooks = {
            "MakeName": self.ida_hook_rename,
            "EditFunction": self.ida_hook_rename,
            "RebaseProgram": self.ida_hook_rebase,
        }

        self.ida_hooks = CapaExplorerIdaHooks(self.ida_hook_screen_ea_changed, action_hooks)
        self.ida_hooks.hook()

    def unload_ida_hooks(self):
        """unload IDA Pro UI hooks

        must be called before plugin is completely destroyed
        """
        if self.ida_hooks:
            self.ida_hooks.unhook()

    def ida_hook_rename(self, meta, post=False):
        """function hook for IDA "MakeName" and "EditFunction" actions

        called twice, once before action and once after action completes

        @param meta: dict of key/value pairs set when action first called (may be empty)
        @param post: False if action first call, True if action second call
        """
        location = idaapi.get_screen_ea()
        if not location or not capa.ida.helpers.is_func_start(location):
            return

        curr_name = idaapi.get_name(location)

        if post:
            # post action update data model w/ current name
            self.model_data.update_function_name(meta.get("prev_name", ""), curr_name)
        else:
            # pre action so save current name for replacement later
            meta["prev_name"] = curr_name

    def ida_hook_screen_ea_changed(self, widget, new_ea, old_ea):
        """function hook for IDA "screen ea changed" action

        called twice, once before action and once after action completes. this hook is currently only relevant
        for limiting results displayed in the UI

        @param widget: IDA widget type
        @param new_ea: destination ea
        @param old_ea: source ea
        """
        if not self.view_limit_results_by_function.isChecked():
            # ignore if limit checkbox not selected
            return

        if idaapi.get_widget_type(widget) != idaapi.BWN_DISASM:
            # ignore views not the assembly view
            return

        if idaapi.get_func(new_ea) == idaapi.get_func(old_ea):
            # user navigated same function - ignore
            return

        self.limit_results_to_function(idaapi.get_func(new_ea))
        self.view_tree.reset_ui()

    def ida_hook_rebase(self, meta, post=False):
        """function hook for IDA "RebaseProgram" action

        called twice, once before action and once after action completes

        @param meta: dict of key/value pairs set when action first called (may be empty)
        @param post: False if action first call, True if action second call
        """
        if post:
            capa.ida.helpers.inform_user_ida_ui("Running capa analysis again after rebase")
            self.slot_analyze()

    def load_capa_results(self):
        """run capa analysis and render results in UI"""
        progress = ProgressUpdater()
        progress.progress.connect(self.slot_progress)

        extractor = CapaExplorerIdaFeatureExtractor(progress)

        self.init_progress(5)
        progress.update("Calculating analysis...", 0)

        item_count = 5
        for f in extractor.get_functions():
            item_count += 1
            for bb in extractor.get_basic_blocks(f):
                item_count += 1
                for _ in extractor.get_instructions(f, bb):
                    item_count += 1

        self.init_progress(item_count)
        progress.update("Loading rules...", 1)

        # resolve rules directory - check self and settings first, then ask user
        if not self.rule_path:
            if "rule_path" in settings and os.path.exists(settings["rule_path"]):
                self.rule_path = settings["rule_path"]
            else:
                rule_path = self.ask_user_directory()
                if not rule_path:
                    capa.ida.helpers.inform_user_ida_ui("You must select a rules directory to use for analysis.")
                    self.view_info_label.setText("No rules loaded")
                    return
                self.rule_path = rule_path
                settings.user["rule_path"] = rule_path

        logger.debug("-" * 80)
        logger.debug(" Using rules from %s.", self.rule_path)
        logger.debug(" ")
        logger.debug(" You can see the current default rule set here:")
        logger.debug("     https://github.com/fireeye/capa-rules")
        logger.debug("-" * 80)

        try:
            rules = capa.main.get_rules(self.rule_path)
            rule_count = len(rules)
            rules = capa.rules.RuleSet(rules)
        except (IOError, capa.rules.InvalidRule, capa.rules.InvalidRuleSet) as e:
            capa.ida.helpers.inform_user_ida_ui("Failed to load rules from %s" % self.rule_path)
            logger.error("failed to load rules from %s (%s)", self.rule_path, e)
            self.rule_path = ""
            self.view_info_label.setText("No rules loaded")
            self.hide_progress()
            return

        progress.update("Analyzing program capabilities...", 1)

        meta = capa.ida.helpers.collect_metadata()
        capabilities, counts = capa.main.find_capabilities(
            rules, extractor, True
        )
        meta["analysis"].update(counts)

        progress.update("Checking for file limitations...", 1)

        # support binary files specifically for x86/AMD64 shellcode
        # warn user binary file is loaded but still allow capa to process it
        # TODO: check specific architecture of binary files based on how user configured IDA processors
        if idaapi.get_file_type_name() == "Binary file":
            logger.warning("-" * 80)
            logger.warning(" Input file appears to be a binary file.")
            logger.warning(" ")
            logger.warning(
                " capa currently only supports analyzing binary files containing x86/AMD64 shellcode with IDA."
            )
            logger.warning(
                " This means the results may be misleading or incomplete if the binary file loaded in IDA is not x86/AMD64."
            )
            logger.warning(" If you don't know the input file type, you can try using the `file` utility to guess it.")
            logger.warning("-" * 80)

            capa.ida.helpers.inform_user_ida_ui("capa encountered warnings during analysis")

        if capa.main.has_file_limitation(rules, capabilities, is_standalone=False):
            capa.ida.helpers.inform_user_ida_ui("capa encountered warnings during analysis")

        progress.update("Rendering results...", 1)

        # convert results document, render views
        self.doc = capa.render.convert_capabilities_to_result_document(meta, rules, capabilities)
        self.model_data.render_capa_doc(self.doc)
        self.render_capa_doc_mitre_summary()

        progress.update("", 1)
        self.hide_progress()

        self.view_info_label.setText("Loaded %d rules from %s" % (rule_count, self.rule_path))

    def render_capa_doc_mitre_summary(self):
        """render MITRE ATT&CK results"""
        tactics = collections.defaultdict(set)

        for rule in rutils.capability_rules(self.doc):
            if not rule["meta"].get("att&ck"):
                continue

            for attack in rule["meta"]["att&ck"]:
                tactic, _, rest = attack.partition("::")
                if "::" in rest:
                    technique, _, rest = rest.partition("::")
                    subtechnique, _, id = rest.rpartition(" ")
                    tactics[tactic].add((technique, subtechnique, id))
                else:
                    technique, _, id = rest.rpartition(" ")
                    tactics[tactic].add((technique, id))

        column_one = []
        column_two = []

        for (tactic, techniques) in sorted(tactics.items()):
            column_one.append(tactic.upper())
            # add extra space when more than one technique
            column_one.extend(["" for i in range(len(techniques) - 1)])

            for spec in sorted(techniques):
                if len(spec) == 2:
                    technique, id = spec
                    column_two.append("%s %s" % (technique, id))
                elif len(spec) == 3:
                    technique, subtechnique, id = spec
                    column_two.append("%s::%s %s" % (technique, subtechnique, id))
                else:
                    raise RuntimeError("unexpected ATT&CK spec format")

        self.view_attack.setRowCount(max(len(column_one), len(column_two)))

        for (row, value) in enumerate(column_one):
            self.view_attack.setItem(row, 0, self.render_new_table_header_item(value))

        for (row, value) in enumerate(column_two):
            self.view_attack.setItem(row, 1, QtWidgets.QTableWidgetItem(value))

        # resize columns to content
        self.view_attack.resizeColumnsToContents()

    def render_new_table_header_item(self, text):
        """create new table header item with our style

        @param text: header text to display
        """
        item = QtWidgets.QTableWidgetItem(text)
        item.setForeground(QtGui.QColor(88, 139, 174))
        font = QtGui.QFont()
        font.setBold(True)
        item.setFont(font)
        return item

    def ida_reset(self):
        """reset plugin UI

        called when user selects plugin reset from menu
        """
        self.view_limit_results_by_function.setChecked(False)
        self.view_search_bar.setText("")
        self.model_data.reset()
        self.view_tree.reset_ui()

    def slot_analyze(self):
        """run capa analysis and reload UI controls

        called when user selects plugin reload from menu
        """
        self.range_model_proxy.invalidate()
        self.search_model_proxy.invalidate()
        self.model_data.clear()
        self.load_capa_results()
        self.ida_reset()

    def slot_reset(self, checked):
        """reset UI elements

        e.g. checkboxes and IDA highlighting
        """
        self.ida_reset()

    def slot_menu_bar_hovered(self, action):
        """display menu action tooltip

        @param action: QtWidgets.QAction*

        @reference: https://stackoverflow.com/questions/21725119/why-wont-qtooltips-appear-on-qactions-within-a-qmenu
        """
        QtWidgets.QToolTip.showText(
            QtGui.QCursor.pos(), action.toolTip(), self.view_menu_bar, self.view_menu_bar.actionGeometry(action)
        )

    def slot_checkbox_limit_by_changed(self, state):
        """slot activated if checkbox clicked

        if checked, configure function filter if screen location is located in function, otherwise clear filter

        @param state: checked state
        """
        if state == QtCore.Qt.Checked:
            self.limit_results_to_function(idaapi.get_func(idaapi.get_screen_ea()))
        else:
            self.range_model_proxy.reset_address_range_filter()

        self.view_tree.reset_ui()

    def limit_results_to_function(self, f):
        """add filter to limit results to current function

        adds new address range filter to include function bounds, allowing basic blocks matched within a function
        to be included in the results

        @param f: (IDA func_t)
        """
        if f:
            self.range_model_proxy.add_address_range_filter(f.start_ea, f.end_ea)
        else:
            # if function not exists don't display any results (assume address never -1)
            self.range_model_proxy.add_address_range_filter(-1, -1)

    def slot_limit_results_to_search(self, text):
        """limit tree view results to search matches

        reset view after filter to maintain level 1 expansion
        """
        self.search_model_proxy.set_query(text)
        self.view_tree.reset_ui(should_sort=False)

    def ask_user_directory(self):
        """create Qt dialog to ask user for a directory"""
        return str(QtWidgets.QFileDialog.getExistingDirectory(self.parent, "Select rules directory", self.rule_path))

    def slot_change_rules_dir(self):
        """allow user to change rules directory

        user selection stored in settings for future runs
        """
        rule_path = self.ask_user_directory()
        if not rule_path:
            logger.warning("no rules directory selected. nothing to do.")
            return

        self.rule_path = rule_path
        settings.user["rule_path"] = rule_path

        if 1 == idaapi.ask_yn(1, "Run analysis now?"):
            self.slot_analyze()

    def slot_progress(self, text, value):
        """ """
        if text:
            self.set_view_info_label(text)
        self.update_progress(value)

    def init_progress(self, count, value=0):
        """configure progress bar

        show progress bar in UI once configured

        @param count: number of items to track
        @param value: initial progress value
        """
        self.view_progress_bar.reset()
        self.view_progress_bar.setRange(0, count)
        self.view_progress_bar.setValue(value)

        self.view_progress_bar.show()

    def update_progress(self, value):
        """increase progress value

        @param value: value to increase
        """
        self.view_progress_bar.setValue(self.view_progress_bar.value() + value)

    def hide_progress(self):
        """reset and hide progress bar"""
        self.view_progress_bar.hide()

    def set_view_info_label(self, text):
        """set text displayed by information label"""
        self.view_info_label.setText(text)
