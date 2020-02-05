from PyQt5.QtCore import Qt, QAbstractItemModel, QAbstractTableModel, QModelIndex
from PyQt5.QtGui import QBrush
from elftools.dwarf.locationlists import LocationParser, LocationExpr
from elftools.dwarf.dwarf_expr import GenericExprDumper, DW_OP_opcode2name
from elftools.dwarf.descriptions import _DESCR_DW_LANG

#------------------------------------------------
# DIE formatter
#------------------------------------------------

_blue_brush = QBrush(Qt.GlobalColor.blue)
_ltgrey_brush = QBrush(Qt.GlobalColor.lightGray)

_ll_headers = ("Attribute", "Offset", "Form", "Raw", "Value")
_noll_headers = ("Attribute", "Form", "Value")
_meta_desc = ('DIE offset', 'Has children') # Anything else?

def get_cu_base(die):
    top_die = die.cu.get_top_DIE()
    if 'DW_AT_low_pc' in top_die.attributes:
        return top_die.attributes['DW_AT_low_pc'].value
    elif 'DW_AT_entry_pc' in top_die.attributes:
        return top_die.attributes['DW_AT_entry_pc'].value
    # TODO: ranges?
    else:
        raise ValueError("Can't find the base address for the location list")

# Format: op arg, arg...
class ExprDumper(GenericExprDumper):
    def __init__(self, structs, prefix, hex):
        GenericExprDumper.__init__(self, structs)
        self.prefix = prefix
        self.hex = hex

    def set_prefix(self, prefix):
        self.prefix = prefix

    def set_hex(self, hex):
        self.hex = hex        
    
    def format_arg(self, s):
        if isinstance(s, str):
            return s
        elif isinstance(s, int):
            return hex(s) if self.hex else str(s)
        elif isinstance(s, bytes): # DW_OP_implicit_value has a bytes argument
            return s.hex() # Python 3.5+
        else: # Assuming a subexpression TODO: check if it's iterable
            return '{' + '; '.join(s) + '}'

    def _dump_to_string(self, opcode, opcode_name, args):
        # Challenge: for nested expressions, args is a list with a list of commands
        # For those, the format is: op {op arg, arg; op arg, arg}
        # Can't just check for iterable, str is iterable too
        if opcode_name.startswith('DW_OP_') and not self.prefix:
            opcode_name = opcode_name[6:]

        if args:
            args = [self.format_arg(s) for s in args]
            args = ', '.join(args)
            return opcode_name + ' ' + args
        else:
            return opcode_name

# TODO: cache expr dumper on CU level, not here
class DIETableModel(QAbstractTableModel):
    def __init__(self, die, prefix, lowlevel, hex):
        QAbstractTableModel.__init__(self)
        self.prefix = prefix
        self.lowlevel = lowlevel
        self.hex = hex
        self.die = die
        self.attributes = die.attributes
        self.keys = list(die.attributes.keys())
        self._exprdumper = None
        self.headers = _ll_headers if self.lowlevel else _noll_headers
        self.meta_count = 2 if lowlevel else 0

    def headerData(self, section, ori, role):
        if ori == Qt.Orientation.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]

    def rowCount(self, parent):
        return len(self.keys) + self.meta_count

    def columnCount(self, parent):
        return len(self.headers)

    def parse_location(self, attr):
        di = self.die.dwarfinfo
        if di._locparser is None:
            di._locparser = LocationParser(di.location_lists())
        if self._exprdumper is None:
            self._exprdumper = ExprDumper(self.die.cu.structs, self.prefix, self.hex)
        return di._locparser.parse_from_attribute(attr, self.die.cu['version'])

    def data(self, index, role):
        row = index.row()
        return self.attr_data(index, role) if row >= self.meta_count else self.meta_data(index, role)

    def attr_data(self, index, role):
        row = index.row() - self.meta_count
        key = self.keys[row]
        attr = self.attributes[key]
        if role == Qt.DisplayRole:
            col = index.column()
            if col == 0:
                # Unknown keys come across as ints
                return key if self.prefix or not str(key).startswith('DW_AT_')  else key[6:]
            elif col == 1:
                return hex(attr.offset) if self.lowlevel else self.format_form(attr.form)
            elif col == 2:
                return self.format_form(attr.form) if self.lowlevel else self.format_value(attr)
            elif col == 3:
                return self.format_raw(attr) if self.lowlevel else self.format_value(attr)
            elif col == 4:
                return self.format_value(attr)
        elif role == Qt.ToolTipRole:
            if attr.form in ('DW_FORM_ref1', 'DW_FORM_ref2', 'DW_FORM_ref4', 'DW_FORM_ref8', 'DW_FORM_ref_addr'):
                return "Double-click to follow"
            elif attr.form in ('DW_FORM_ref_sig8', 'DW_FORM_ref_sup4', 'DW_FORM_ref_sup8'):
                return "Unsupported reference format"
        elif role == Qt.ForegroundRole:
            if attr.form in ('DW_FORM_ref1', 'DW_FORM_ref2', 'DW_FORM_ref4', 'DW_FORM_ref8', 'DW_FORM_ref_addr'):
                return _blue_brush

    def meta_data(self, index, role):
        row = index.row()
        if role == Qt.DisplayRole:
            col = index.column()
            if col == 0:
                return _meta_desc[row]
            elif col == (4 if self.lowlevel else 2):
                if row == 0:
                    return hex(self.die.offset)
                elif row == 1:
                    return str(self.die.has_children)
        elif role == Qt.BackgroundRole:
            return _ltgrey_brush

    # End of Qt callbacks

    # Big DIE attribute value interpreter
    def format_value(self, attr):
        key = attr.name
        val = attr.value
        form = attr.form
        if isinstance(val, bytes):
            return val.decode('utf-8')
        elif form == 'DW_FORM_addr' and isinstance(val, int):
            return hex(val)
        elif form in ('DW_FORM_ref1', 'DW_FORM_ref2', 'DW_FORM_ref4', 'DW_FORM_ref8', 'DW_FORM_ref_addr'):
            return "Ref: 0x%X" % val # There are several other reference forms in the spec
        elif LocationParser.attribute_has_location(attr, self.die.cu['version']):
            ll = self.parse_location(attr)
            if isinstance(ll, LocationExpr):
                return '; '.join(self._exprdumper.dump(ll.loc_expr))
            else:
                return "Loc list: 0x%X" % attr.value
        elif key == 'DW_AT_language':
            return "%d %s" % (val, _DESCR_DW_LANG[val]) if val in _DESCR_DW_LANG else val
        elif key == 'DW_AT_decl_file':
            if self.die.cu._lineprogram is None:
                self.die.cu._lineprogram = self.die.dwarfinfo.line_program_for_CU(self.die.cu)
            return "%d: %s" % (val, self.die.cu._lineprogram.header.file_entry[val-1].name.decode('ASCII')) if val > 0 else "0: (N/A)"
        elif key == 'DW_AT_stmt_list':
            return 'LNP at 0x%x' % val 
        else:
            return hex(val) if isinstance(val, int) and self.hex else str(attr.raw_value)

    def format_form(self, form):
        return form if self.prefix or not str(form).startswith('DW_FORM_') else form[8:]

    def format_raw(self, attr):
        return hex(attr.raw_value) if isinstance(attr.raw_value, int) and self.hex else str(attr.raw_value)

    def display_DIE(self, die):
        rows_was = len(self.keys)
        self.die = die
        self.attributes = die.attributes
        self.keys = list(die.attributes.keys())
        rows_now = len(self.keys)
        if rows_was < rows_now:
            self.rowsInserted.emit(QModelIndex(), rows_was, rows_now-1)
        elif rows_was > rows_now:
            self.rowsRemoved.emit(QModelIndex(), rows_now, rows_was-1)
        self.dataChanged.emit(self.createIndex(0, 0), self.createIndex(rows_now-1, 3))

    def set_prefix(self, prefix):
        if prefix != self.prefix:
            self.prefix = prefix
            self.dataChanged.emit(self.createIndex(0, 2), self.createIndex(len(self.keys)-1, 3))
            if self._exprdumper:
                self._exprdumper.set_prefix(prefix)

    def set_lowlevel(self, lowlevel):
        if lowlevel != self.lowlevel:
            self.lowlevel = lowlevel
            self.headers = _ll_headers if self.lowlevel else _noll_headers
            if lowlevel:
                self.beginInsertColumns(QModelIndex(), 2, 3)
                self.endInsertColumns()
                self.meta_count = 2
                self.rowsInserted.emit(QModelIndex(), 0, self.meta_count - 1)
            else:
                self.beginRemoveColumns(QModelIndex(), 2, 3)
                self.endRemoveColumns()
                self.rowsRemoved.emit(QModelIndex(), 0, self.meta_count - 1)
                self.meta_count = 0

    def set_hex(self, hex):
        if hex != self.hex:
            self.hex = hex
            if self._exprdumper:
                self._exprdumper.set_hex(hex)
            self.dataChanged.emit(self.createIndex(0, 0), self.createIndex(len(self.keys)-1, 0))

    # Returns a table model for the attribute details table
    # For attributes that refer to larger data structures (ranges, locations), makes sense spell it out into a table
    # Row is metadata unaware
    def get_attribute_details(self, index):
        row = index.row()
        if row >= self.meta_count:
            row -= self.meta_count
            key = self.keys[row]
            attr = self.attributes[key]
            form = attr.form
            if key == "DW_AT_ranges":
                di = self.die.dwarfinfo
                if not di._ranges:
                    di._ranges = di.range_lists()
                if not di._ranges: # Absent in the DWARF file
                    return None
                ranges = di._ranges.get_range_list_at_offset(attr.value)
                # TODO: handle base addresses. Never seen those so far...
                cu_base = get_cu_base(self.die)
                return GenericTableModel(("Start offset", "End offset"),
                    ((hex(cu_base + r.begin_offset), hex(cu_base + r.end_offset)) for r in ranges))
            elif LocationParser.attribute_has_location(attr, self.die.cu['version']):
                ll = self.parse_location(attr)
                if isinstance(ll, LocationExpr):
                    return GenericTableModel(("Command",), ((cmd,) for cmd in self._exprdumper.dump(ll.loc_expr)))
                else:
                    cu_base = get_cu_base(self.die)
                    return GenericTableModel(("Start offset", "End offset", "Expression"),
                        ((hex(cu_base + l.begin_offset), hex(cu_base + l.end_offset), '; '.join(self._exprdumper.dump(l.loc_expr))) for l in ll))
            elif key == 'DW_AT_stmt_list':
                if self.die.cu._lineprogram is None:
                    self.die.cu._lineprogram = self.die.dwarfinfo.line_program_for_CU(self.die.cu)
                lpe = self.die.cu._lineprogram.get_entries()
                files = self.die.cu._lineprogram.header.file_entry
                def format_state(state):
                    return (hex(state.address),
                        files[state.file-1].name.decode('ASCII') if state.file > 0 else '(N/A)',
                        state.line)
                states = [format_state(e.state) for e in lpe if e.state]
                # TODO: low level flavor with extra details
                # TODO: commands vs states
                return GenericTableModel(('Address', 'File', 'Line'), states)
        return None

    # Returns (cu, die_offset) or None if not a navigable
    def ref_target(self, index):
        attr = self.attributes[self.keys[index.row() - self.meta_count]]
        if attr.form in ('DW_FORM_ref1', 'DW_FORM_ref2', 'DW_FORM_ref4', 'DW_FORM_ref8'):
            return (self.die.cu, attr.value + self.die.cu.cu_offset)
        elif attr.form == 'DW_FORM_ref_addr':
            prev_cu = None
            for cu in self.die.dwarfinfo._CUs: # Don't reparse CUs, reuse cached ones
                if prev_cu is None:
                    prev_cu = cu
                elif cu.cu_offset > attr.value:
                    return (prev_cu, attr.value)
                else:
                    prev_cu = cu
        return None


class GenericTableModel(QAbstractTableModel):
    def __init__(self, headers, values):
        QAbstractTableModel.__init__(self)
        self.headers = headers
        self.values = tuple(values)

    def headerData(self, section, ori, role):
        if ori == Qt.Orientation.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]

    def rowCount(self, parent):
        return len(self.values)

    def columnCount(self, parent):
        return len(self.headers)

    def data(self, index, role):
        if role == Qt.DisplayRole:
            return self.values[index.row()][index.column()]