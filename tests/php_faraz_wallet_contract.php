<?php
// Synthetic native-ledger contract; deliberately independent of WordPress.
define('ABSPATH', __DIR__.'/'); define('ARRAY_A', 'ARRAY_A');
function register_activation_hook($f,$c) {} function add_action($e,$c) {}
class WP_Error { public $code; function __construct($c,$m,$d) { $this->code=$c; } }
function is_wp_error($v) { return $v instanceof WP_Error; }
function sanitize_text_field($v) { return strip_tags($v); }
function get_userdata($id) { return $id===7 ? (object)array('ID'=>7) : false; }
function clean_user_cache($id) {} function current_time($a,$b=false) { return '2026-11-03 10:00:00'; }
function wp_json_encode($v) { return json_encode($v); }
function get_woocommerce_currency() { return 'IRT'; } function wc_get_price_decimals() { return 0; }
function wc_format_decimal($v,$d) { return number_format((float)$v,$d,'.',''); }
function farazsms_wallet_table() { return 'wp_farazsms_user_wallet'; }
function farazsms_wallet_balance($id) { return 0; }
class Request extends ArrayObject { function get_param($n) { return $this[$n] ?? null; } }
class NativeDB {
    public $prefix='wp_', $last_error='', $saved=array(), $native=array(), $locks=0;
    function prepare($q,...$args) { return array($q,$args); }
    function esc_like($s) { return $s; }
    function get_var($q) { if ($q==='SELECT VERSION()') return '10.11.6-MariaDB'; if (strpos($q[0],'RELEASE_LOCK')!==false) $this->locks--; else $this->locks++; return 1; }
    function get_row($q,$f) { return $this->saved[$q[1][0]] ?? null; }
    function get_results($q,$f) { return array_values(array_filter($this->native,function($r)use($q){ return strpos($r['description'],trim($q[1][0],'%'))!==false; })); }
    function insert($t,$r) { $this->saved[$r['operation_key']]=$r; return 1; }
    function update($t,$r,$where) { $key=$where['operation_key']; $this->saved[$key]=array_merge($this->saved[$key],$r); return 1; }
}
$wpdb=new NativeDB(); $calls=0; $lose=false; $fail=false;
function farazsms_wallet_add_credit($id,$amount,$args) {
    global $wpdb,$calls,$lose,$fail; $calls++;
    if ($args['expiry_days']!==0 || $args['dedupe']!==false || $args['source']!=='afrand_panel') throw new Exception('Native contract changed');
    if ($fail) return false;
    $wpdb->native[]=array('transaction_id'=>$calls,'user_id'=>$id,'amount'=>$amount,'source'=>$args['source'],'description'=>$args['description'],'status'=>'expired','used_amount'=>$amount);
    return $lose ? false : $calls;
}
require __DIR__.'/../wordpress/afrand-faraz-wallet-bridge/afrand-faraz-wallet-bridge.php';
function check($v,$message) { if (!$v) throw new Exception($message); }
$p=array('request_id'=>str_repeat('a',64),'customer_id'=>7,'amount'=>'123','currency'=>'IRT','note'=>'Sale','confirm'=>true);
$r=Afrand_Faraz_Wallet_Bridge::credit(new Request($p));
check(!is_wp_error($r) && $r['amount']==='123' && !$r['replayed'] && $calls===1,'Initial native credit');
$r=Afrand_Faraz_Wallet_Bridge::credit(new Request($p));
check($r['replayed'] && $calls===1,'Replay duplicated');
$r=Afrand_Faraz_Wallet_Bridge::credit(new Request(array_merge($p,array('amount'=>'124'))));
check(is_wp_error($r) && $r->code==='afrand_conflict' && $calls===1,'Conflict accepted');
$p['request_id']=str_repeat('b',64); $lose=true;
check(is_wp_error(Afrand_Faraz_Wallet_Bridge::credit(new Request($p))),'Lost native response');
$r=Afrand_Faraz_Wallet_Bridge::operation(new Request(array('key'=>$p['request_id'],'expected_note'=>'Sale')));
check(!is_wp_error($r) && $r['state']==='succeeded' && $calls===2,'Consumed/expired native credit not recovered');
$lose=false; $fail=true; $p['request_id']=str_repeat('c',64);
Afrand_Faraz_Wallet_Bridge::credit(new Request($p));
$r=Afrand_Faraz_Wallet_Bridge::credit(new Request($p));
check(is_wp_error($r) && $r->code==='afrand_unknown' && $calls===3,'Unknown credit reissued');
$p['currency']='IRR'; $p['request_id']=str_repeat('d',64);
check(is_wp_error(Afrand_Faraz_Wallet_Bridge::credit(new Request($p))) && $calls===3,'Currency accepted');
check($wpdb->locks===0,'Lock leaked');
echo "PASS: native amount, replay, conflict, lost response, consumed/expired recovery, unknown outcome, currency, locks (synthetic)\n";
