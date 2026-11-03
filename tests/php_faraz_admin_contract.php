<?php
// Read-only admin rendering contract; no WordPress installation or financial writes.
define('ABSPATH', __DIR__);
$allowed = true;
function current_user_can($cap) { global $allowed; return $allowed && $cap === 'manage_woocommerce'; }
function wp_die($message, $title='', $args=array()) { throw new RuntimeException('denied:' . $args['response']); }
function add_submenu_page(...$args) { if ($args[3] !== 'manage_woocommerce') throw new Exception('Capability'); }
function esc_html($v) { return htmlspecialchars((string)$v, ENT_QUOTES, 'UTF-8'); }
function esc_attr($v) { return esc_html($v); }
function esc_url($v) { return esc_html($v); }
function admin_url($v) { return '/wp-admin/' . $v; }
function add_query_arg($a,$url) { return $url . '?' . http_build_query($a); }
function absint($v) { return abs((int)$v); }
function wp_unslash($v) { return stripslashes($v); }
function sanitize_text_field($v) { return strip_tags($v); }
function get_woocommerce_currency() { return 'IRT'; }
function number_format_i18n($v,$d) { return number_format($v,$d); }
function get_user_meta($id,$key,$single) { return $key === 'billing_phone' ? '09994860773' : ''; }
function get_userdata($id) { return $id===11 ? (object)array('ID'=>11,'display_name'=>'<script>unsafe</script>','user_login'=>'09994860773') : false; }
function update_meta_cache($type,$ids) {}
function wp_list_pluck($rows,$key) { return array_map(function($r)use($key){return $r->$key;},$rows); }
function current_time($type) { return '2026-11-03 12:00:00'; }
if (!in_array('--missing', $argv)) {
    function farazsms_wallet_table() { return 'wp_farazsms_user_wallet'; }
    function farazsms_wallet_balance($id) { return 129; }
}
class AdminDB {
    public $users='wp_users', $usermeta='wp_usermeta', $last_error='', $queries=array(), $empty=false;
    function esc_like($v) { return addcslashes($v, '_%\\'); }
    function prepare($q,...$args) { foreach($args as $v) $q=preg_replace_callback('/%[ds]/',function($m)use($v){return $m[0]==='%d' ? (int)$v : "'" . str_replace("'","''",$v) . "'";},$q,1); return $q; }
    function get_var($q) { $this->queries[]=$q; return $this->empty ? 0 : 41; }
    function get_results($q) {
        $this->queries[]=$q;
        if ($this->empty) return array();
        if (strpos($q,'SELECT u.ID')!==false) return array(get_userdata(11));
        return array((object)array('id'=>1,'amount'=>150,'used_amount'=>21,'status'=>'active','expires_at'=>null,'created_at'=>'2026-11-03','description'=>'<img src=x onerror=alert(1)> [AFRAND:'.str_repeat('a',64).']','source'=>'afrand_panel'));
    }
}
$wpdb=new AdminDB();
require __DIR__.'/../wordpress/afrand-faraz-wallet-bridge/admin-wallets.php';
function check($ok,$message) { if (!$ok) throw new Exception($message); }
function render_admin($args) { $_GET=$args; ob_start(); Afrand_Faraz_Wallet_Admin::render(); return ob_get_clean(); }
Afrand_Faraz_Wallet_Admin::register();
$allowed=false;
ob_start(); try { Afrand_Faraz_Wallet_Admin::render(); throw new Exception('Unauthorized accepted'); } catch(RuntimeException $e) { check($e->getMessage()==='denied:403','403'); } ob_end_clean();
$allowed=true;
if (in_array('--missing',$argv)) {
    check(strpos(render_admin(array()),'notice-error')!==false,'Missing dependency');
    check(!$wpdb->queries,'Queried missing ledger');
    echo "PASS: dependency unavailable, capability denied\n"; exit;
}
$html=render_admin(array('s'=>"۰۹۹۹'_%",'paged'=>99999999));
check(strpos($html,'129 تومان')!==false,'Native balance');
check(strpos($html,'<script>')===false && strpos($html,'&lt;script&gt;')!==false,'Name XSS');
check(strpos(implode('\n',$wpdb->queries),'EXISTS')!==false,'No duplicate metadata joins');
check(strpos(implode('\n',$wpdb->queries),'OFFSET 40')!==false,'Pagination bound');
$html=render_admin(array('user_id'=>11));
check(strpos($html,'150 تومان')!==false && strpos($html,'21 تومان')!==false,'Native ledger amounts');
check(strpos($html,'<img')===false && strpos($html,'AFRAND:')===false,'History escape and marker');
check(strpos(render_admin(array('user_id'=>999)),'کاربر پیدا نشد')!==false,'Missing user');
$wpdb->empty=true;
check(strpos(render_admin(array()),'کاربری پیدا نشد')!==false,'Empty list');
foreach($wpdb->queries as $q) check(!preg_match('/\b(INSERT|UPDATE|DELETE|REPLACE)\b/i',$q),'Read-only');
echo "PASS: permissions, native balance/history, XSS, pagination, prepared search, empty/missing user, read-only\n";
