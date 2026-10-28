<?php
// Isolated operation verification contract. Sanitizer fixtures are synthetic;
// this is not a WordPress/TeraWallet integration test.
define('ABSPATH', __DIR__.'/');
define('ARRAY_A', 'ARRAY_A');
function register_activation_hook($file,$callback) {}
function add_action($event,$callback) {}
class WP_Error {
    public $code;
    function __construct($code,$message,$data) { $this->code=$code; }
}
function is_wp_error($value) { return $value instanceof WP_Error; }
function sanitize_text_field($value) {
    global $sanitizer_input,$canonical;
    $sanitizer_input=$value;
    return $canonical;
}
class TitleDatabase {
    public $prefix='wp_';
    public $row;
    public $unlocks=0;
    function prepare($query,...$values) { return $query; }
    function get_var($query) { if (strpos($query,'RELEASE_LOCK')!==false) $this->unlocks++; return 1; }
    function get_row($query,$format) { return $this->row; }
}
class TitleRequest extends ArrayObject {
    function get_param($name) { return $this->offsetExists($name) ? $this[$name] : null; }
}
$wpdb=new TitleDatabase();
require __DIR__.'/../wordpress/afrand-wallet-bridge/afrand-wallet-bridge.php';
$key=str_repeat('a',64);
$cases=array(array('<b>Sale</b>','Sale'),array('خرید <b>پاییز</b>','خرید پاییز'),
    array("Sale\t \n autumn",'Sale autumn'),array('Sale &amp; &#160; autumn','Sale &amp; &#160; autumn'));
foreach ($cases as $case) {
    list($raw,$canonical)=$case;
    // Existing records contain only the sanitized note; no new metadata needed.
    $saved=array('customer_id'=>7,'amount'=>'5','currency'=>'IRT','note'=>$canonical,'transaction_id'=>100,'state'=>'succeeded');
    $wpdb->row=array('state'=>'succeeded','result_json'=>json_encode($saved));
    $before=$wpdb->row; $sanitizer_input=null;
    $result=Afrand_Wallet_Bridge::operation(new TitleRequest(array('key'=>$key,'expected_note'=>$raw)));
    if (is_wp_error($result) || $result['verified_note']!==$raw || $result['note']!==$canonical || $sanitizer_input!==$raw || $wpdb->row!==$before) throw new Exception('Title verification failed');
    $canonical='different title';
    $conflict=Afrand_Wallet_Bridge::operation(new TitleRequest(array('key'=>$key,'expected_note'=>$raw)));
    if (!is_wp_error($conflict) || $conflict->code!=='afrand_conflict') throw new Exception('Conflict not rejected');
    $legacy=Afrand_Wallet_Bridge::operation(new TitleRequest(array('key'=>$key)));
    if (is_wp_error($legacy) || isset($legacy['verified_note']) || $legacy['note']!==$saved['note']) throw new Exception('Legacy response changed');
}
if ($wpdb->unlocks!==12) throw new Exception('Operation lock leaked');
echo "PASS: 4 title fixtures, conflicts, legacy records/clients and lock release (stub; not WordPress integration)\n";
