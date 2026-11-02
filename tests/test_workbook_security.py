"""Bound hostile OOXML input without dropping legitimate distant records."""
import io
import unittest
from zipfile import ZipFile, ZIP_DEFLATED
from unittest.mock import patch
from defusedxml.common import DefusedXmlException
from panel.accounting import read_accounting_export
from test_accounting import export


def rewrite(stream, transform):
    result = io.BytesIO()
    with ZipFile(stream) as source, ZipFile(result, 'w', ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item.filename, transform(item.filename, source.read(item)))
    result.seek(0)
    return result


class WorkbookSecurityTests(unittest.TestCase):
    def fixture(self):
        return export([['order_ref', 'customer_name', 'customer_phone', 'product_sku', 'quantity'],
                       ['DOC', 'Customer', '09121234567', 'SKU', 1]])

    def test_forged_small_dimension_cannot_hide_customer(self):
        stream = rewrite(self.fixture(), lambda name, raw:
                         raw.replace(b'ref="A1:E2"', b'ref="A1:A1"') if name.endswith('sheet1.xml') else raw)
        parsed = read_accounting_export(stream)
        self.assertEqual(len(parsed['rows']), 1)
        self.assertEqual(parsed['rows'][0]['order_ref'], 'DOC')

    def test_forged_huge_dimension_does_not_expand_grid(self):
        stream = rewrite(self.fixture(), lambda name, raw:
                         raw.replace(b'ref="A1:E2"', b'ref="A1:XFD1048576"') if name.endswith('sheet1.xml') else raw)
        self.assertEqual(len(read_accounting_export(stream)['rows']), 1)

    def test_distant_real_rows_and_columns_reject_entire_file_before_loading(self):
        for coordinate in (b'CW2', b'A10001'):
            with self.subTest(coordinate=coordinate):
                stream = rewrite(self.fixture(), lambda name, raw:
                                 raw.replace(b'r="A2"', b'r="' + coordinate + b'"') if name.endswith('sheet1.xml') else raw)
                with patch('panel.accounting.load_workbook') as loader, self.assertRaises(ValueError):
                    read_accounting_export(stream)
                loader.assert_not_called()

    def test_compressed_expansion_rejected_before_openpyxl(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('xl/sharedStrings.xml', b' ' * (12 * 1024 * 1024 + 1))
        self.assertLess(stream.tell(), 20000)
        stream.seek(0)
        with patch('panel.accounting.load_workbook') as loader, self.assertRaises(ValueError):
            read_accounting_export(stream)
        loader.assert_not_called()

    def test_xml_entities_rejected(self):
        stream = rewrite(self.fixture(), lambda name, raw:
                         b'<!DOCTYPE worksheet [<!ENTITY x "unsafe">]>' + raw.replace(b'Customer', b'&x;')
                         if name.endswith('sheet1.xml') else raw)
        with self.assertRaises(DefusedXmlException):
            read_accounting_export(stream)
